"""Phase 2 — LLM status augmentation for SEC 10-K extractor.

Phase 1 (rules-only) catches the unambiguous ~80% for free. Phase 2 LLM only
escalates the cases Phase 1 is uncertain about — keeping cost in check and
respecting the rule "if Phase 1 is confident, trust Phase 1".

Triggers (`should_augment_status`):
  - Phase 1 returned `extracted` but content < 500 chars  (suspicious — too short
    to be substantive)
  - Phase 1 returned `extracted` but content contains "incorporated" or "by
    reference" (within-document or proxy by-ref Phase 1 missed)
  - Phase 1 returned `extracted` but content contains a "See Item N" pattern
    (within-document cross-reference — common in 1995-era banking 10-Ks)
  - Phase 1 returned `extracted` but content contains "remaining/additional/
    other information ... Item" (canonical partial-disclosure phrasing)

Voting:
  Uses `shared.llm_client.vote_role("extractor_aug", ...)` which dispatches
  K=N parallel calls and majority-votes the parsed status. K controlled by
  EXTRACTOR_AUG_MODELS env var. Tie-break and total-failure both fall back
  to the original Phase 1 result (deterministic).

Cost discipline:
  Augmentation only runs on items that pass `should_augment_status`. On
  Apple 2024 (Phase 1 already 100%) this fires for 0-2 items; on a noisy
  filing it might fire for 5-8. With K=3 ensemble that's at most ~25 LLM
  calls per filing, ~250 across the 10-filing eval set.
"""

from __future__ import annotations

import json
import logging
import re
from typing import get_args

from shared.llm_client import VoteResult, vote_role
from workers.extractor.schema import Status

log = logging.getLogger(__name__)

_VALID_STATUSES = frozenset(get_args(Status))

# Triggers — Phase 1 is uncertain when content_text shows these patterns.
_RE_INCORPORATED_HINT = re.compile(
    r"\b(?:incorporated\s+(?:herein\s+)?by\s+reference|by\s+reference\s+(?:to|herein))\b",
    re.IGNORECASE,
)
_RE_SEE_ITEM_HINT = re.compile(
    r"\bsee\s+Item\s+\d+[A-C]?\b",
    re.IGNORECASE,
)
_RE_REMAINING_INFO_HINT = re.compile(
    r"\b(?:remaining|additional|other)\s+information\s+(?:required\s+|called\s+for\s+)?"
    r"by\s+this\s+[Ii]tem",
    re.IGNORECASE,
)


def should_augment_status(phase1_status: Status, content_text: str) -> str | None:
    """Return a short reason string if Phase 2 LLM should re-judge, or None.

    We only escalate when Phase 1 said `extracted` but the content has
    suspicious markers. Other Phase 1 verdicts (reserved, not_applicable,
    incorporated_by_reference, partial) are typically high-confidence and
    don't need LLM second-opinion.
    """
    if phase1_status != "extracted":
        return None
    text = content_text or ""
    # Cross-reference TOC items (GE 2021 style) carry the TOC entry as their
    # content_text. The status_hint from the TOC is more authoritative than
    # anything an LLM can infer from a 50-char snippet — never augment these.
    if text.lstrip().startswith("[Cross-reference TOC]"):
        return None
    n = len(text)
    if n < 500:
        return f"short content ({n} chars) — possible missed by-reference"
    if _RE_INCORPORATED_HINT.search(text):
        return "contains 'incorporated by reference' phrase"
    if _RE_SEE_ITEM_HINT.search(text):
        return "contains 'see Item N' within-document cross-reference"
    if _RE_REMAINING_INFO_HINT.search(text):
        return "contains 'remaining/additional information by this Item' (partial signal)"
    return None


_PROMPT_SYSTEM = """\
You classify SEC 10-K Item content into one of five status categories.

Categories:
- extracted: substantive in-line disclosure (the body of the item is here)
- incorporated_by_reference: WHOLE item refers to another document (e.g. Proxy Statement DEF 14A) instead of containing content here
- not_applicable: explicit "Not applicable", "None", or equivalent
- reserved: "[Reserved]" placeholder (Item 6 since 2021)
- partial: MIXED — some content inline AND some incorporated by reference

Within-document references (e.g. "See Item 13 below") count as incorporated_by_reference because the item's body is elsewhere in the filing.

A short paragraph (<500 chars) that mentions an inline detail BUT also says "Item X is incorporated by reference" should be classified as `partial`.

Reply with exactly this JSON structure on a single line — no prose, no markdown fences:
{"status": "<one of the 5 categories>", "confidence": <0.0-1.0>, "rationale": "<one sentence>"}
"""


def _build_messages(item_number: str, item_title: str, content_text: str,
                    phase1_status: Status, trigger_reason: str) -> list[dict]:
    # Cap content length to keep token cost in check — 4000 chars covers the
    # decisive opening of most items; long bodies don't add classification signal.
    content_excerpt = content_text[:4000]
    if len(content_text) > 4000:
        content_excerpt += "\n[... truncated for brevity ...]"
    user = (
        f"10-K Item {item_number} ({item_title})\n"
        f"Phase-1 (rules-based) verdict: {phase1_status}\n"
        f"Why we're asking you to re-judge: {trigger_reason}\n"
        f"\n--- Item content ---\n"
        f"{content_excerpt}\n"
        f"--- end content ---\n"
        f"\nReturn the JSON only."
    )
    return [
        {"role": "system", "content": _PROMPT_SYSTEM},
        {"role": "user", "content": user},
    ]


def _parse_status_response(raw: str) -> Status:
    """Parse the LLM response into a Status. Raises ValueError if malformed."""
    text = raw.strip()
    # Strip leading code fences if the model added them despite our instruction
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # Try direct JSON first; fall back to extracting the {...} substring
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"no JSON object found in response: {raw[:200]!r}") from None
        parsed = json.loads(m.group(0))
    status_val = parsed.get("status")
    if not isinstance(status_val, str):
        raise ValueError(f"missing 'status' string in response: {parsed!r}")
    cleaned = status_val.strip().lower()
    if cleaned not in _VALID_STATUSES:
        raise ValueError(f"invalid status {cleaned!r}; must be one of {_VALID_STATUSES}")
    return cleaned  # type: ignore[return-value]


async def augment_status(
    item_number: str,
    item_title: str,
    content_text: str,
    phase1_status: Status,
    trigger_reason: str,
) -> VoteResult:
    """Run LLM ensemble vote to re-judge an item's status.

    Returns a VoteResult. The caller decides whether to override Phase 1
    based on `confidence` (e.g. require ≥ 2/3 votes to overturn).
    """
    messages = _build_messages(
        item_number, item_title, content_text, phase1_status, trigger_reason,
    )
    return await vote_role(
        "extractor_aug",
        messages=messages,
        parser=_parse_status_response,
        fallback=phase1_status,
        max_tokens=1024,
        temperature=0.2,
        timeout=90.0,
    )


# ── Decision policy: when to override Phase 1 ────────────────────────────────

# Confidence threshold to overturn Phase 1. Set just above 0.5 — a 2/3 majority
# is enough, but a 1-1-1 tie (confidence=0) is not.
_OVERRIDE_THRESHOLD = 0.51


def should_override_phase1(vote: VoteResult, phase1_status: Status) -> bool:
    """Decide whether to apply the LLM vote's status over Phase 1's.

    Override only when:
      - vote actually produced a result (not all-failed)
      - vote pick disagrees with Phase 1
      - vote confidence is above threshold
    """
    if vote.fallback_used or vote.confidence < _OVERRIDE_THRESHOLD:
        return False
    return vote.pick != phase1_status
