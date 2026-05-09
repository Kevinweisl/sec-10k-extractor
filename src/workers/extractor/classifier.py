"""Rule-based status classifier for 10-K Items.

Phase 1 of the hybrid pipeline: regex-only, no LLM. Catches the unambiguous
cases (~80% of items in canonical filings) for free. Edge cases get escalated
to LLM in Phase 2 (`llm_assist.py`).

Status reference (see schema.py): extracted | incorporated_by_reference |
not_applicable | reserved | partial | non_standard.

Pre-processing notes
- edgartools prepends "Item N. Title" to every item body and sometimes
  bleeds the start of the next item onto the end (page-break artifact).
  We strip the leading 'Item N(letter). Title' header and look at the
  first ~400 chars of the BODY for short-answer statuses; this avoids
  picking up '[Reserved]' or 'not applicable' from a neighboring item.
"""

from __future__ import annotations

import re

# 'Item 6. [Reserved]' since 2021 (after Selected Financial Data was deleted).
# Only match when [Reserved] is the actual content, not a stray bleed-over from a
# neighboring item — handled by checking the body head only.
_RE_RESERVED_HEAD = re.compile(r"\[\s*reserved\s*\]", re.IGNORECASE)

# 'Not applicable' / 'None' as the whole item body (allow trailing footer noise).
_RE_NOT_APPLICABLE_HEAD = re.compile(
    r"^\s*(not\s+applicable|none)\.?\s*$", re.IGNORECASE,
)

# Strip the 'Item N. Title' header that edgartools includes in every item body.
# The title can use \xa0 spaces (non-breaking) and en/em dashes.
_RE_ITEM_HEADER = re.compile(
    r"^\s*item\s+\d+[A-C]?\.?[\s\xa0]*[^\n]*?\n",
    re.IGNORECASE,
)
# fallback: header on a single line (no newline) — strip up to first 2+ spaces
_RE_ITEM_HEADER_INLINE = re.compile(
    r"^\s*item\s+\d+[A-C]?\.?[\s\xa0]+[^\s][^\n]{0,120}?[\s\xa0]{2,}",
    re.IGNORECASE,
)
# Trailing footer/page noise to ignore. Two shapes:
#   1) Full footer:  '... Apple Inc. | 2024 Form 10-K | 5  PART II'
#   2) Bare bleed:   '... PART III'  (just the next-part marker, no company line)
# Constrained company name to 1-3 space-separated word groups (e.g. "Apple Inc.",
# "Berkshire Hathaway Inc.") so the regex doesn't eat substantive content.
_RE_TRAILING_FOOTER = re.compile(
    r"(?:"
    # Form-1: company line + page number, anchored after a sentence-ending
    # punctuation so we don't eat substantive content like "DisclosuresNot
    # applicable.Apple Inc. | 2024..." starting from "DisclosuresNot".
    r"(?<=[.!?])\s*[A-Z][\w.®]*(?:\s+[\w.®]+){0,2}\s*\|\s*\d{4}\s+Form\s+10-K\s*\|\s*\d+\s*(?:PART\s+[IVX]+)?"
    r"|"
    # Form-2: bare next-part header bleed
    r"\s*PART\s+[IVX]+"
    r")\s*$",
    re.IGNORECASE,
)

# Canonical "this item's content is in the proxy" language.
# Verified against Apple 2024 10-K Items 11-14 (literal copies of the same template).
_RE_INCORPORATED = re.compile(
    r"\b(?:information\s+(?:required\s+(?:by\s+this\s+[Ii]tem\s+)?|called\s+for\s+by\s+this\s+[Ii]tem))\b"
    r"[^.]{0,300}?\b(?:incorporated\s+(?:herein\s+)?by\s+reference|"
    r"will\s+be\s+(?:included|set\s+forth|contained|presented)\s+in\s+the\s+(?:\d{4}\s+)?[Pp]roxy\s+[Ss]tatement)\b",
    re.IGNORECASE | re.DOTALL,
)

# Negative control: the boilerplate that EXPLICITLY says websites are NOT incorporated.
# Apple 2024 10-K cover page contains this; must not match _RE_INCORPORATED for it.
_RE_NEGATIVE_WEBSITE = re.compile(
    r"websites?\s+(?:referenced[^.]{0,80}|on\s+which[^.]{0,80})not\s+incorporated\s+by\s+reference",
    re.IGNORECASE,
)

# A loose marker — "see X" that probably indicates by-reference even without the canonical phrase.
_RE_LOOSE_SEE = re.compile(
    r"^\s*see\s+(?:our\s+|the\s+)?[\d\s]*[Pp]roxy\s+[Ss]tatement",
    re.IGNORECASE | re.MULTILINE,
)

# 'The remaining information required by this Item' / 'additional information'
# / 'other information required' — these clauses signal mixed inline + by-ref.
_RE_REMAINING_INFO = re.compile(
    r"\b(?:remaining|additional|other)\s+information\s+(?:required\s+|called\s+for\s+)?by\s+this\s+[Ii]tem",
    re.IGNORECASE,
)


def _strip_trailing_footer(text: str) -> str:
    """Drop the page-bottom 'Apple Inc. | 2024 Form 10-K | 5' style footer."""
    return _RE_TRAILING_FOOTER.sub("", text).rstrip()


# strip just the 'Item N.' prefix, NOT the title — to keep '[Reserved]' / 'None'
# visible when they sit on the same line as the title. Footnote: edgartools uses
# non-breaking spaces (\xa0) which we normalize to ASCII space first.
_RE_ITEM_PREFIX = re.compile(r"^\s*item\s+\d+[A-C]?\.?\s*", re.IGNORECASE)


def _strip_item_prefix(text: str) -> str:
    """Strip just `Item N.` (not the title); used to match `Not applicable` /
    `None` / `[Reserved]` on lines that include both the title and the body."""
    if not text:
        return ""
    s = text.replace("\xa0", " ").lstrip()
    m = _RE_ITEM_PREFIX.match(s)
    return s[m.end():] if m else s


def classify_status(text: str) -> str:
    """Classify an item body. Returns one of the Status literals.

    Heuristics in priority order:
      1. body length < 300 chars AND contains `[Reserved]` -> reserved
      2. body length < 200 chars AND ends with 'Not applicable' / 'None' -> not_applicable
      3. canonical incorporated-by-reference clause (and not the negative
         control) -> incorporated_by_reference if dominant, partial if mixed
      4. otherwise -> extracted
    """
    if not text:
        return "extracted"

    cleaned = _strip_trailing_footer(text)
    body_after_prefix = _strip_item_prefix(cleaned).strip()
    short = len(body_after_prefix) < 300

    # 1. Reserved
    if short and _RE_RESERVED_HEAD.search(body_after_prefix):
        return "reserved"

    # 2. Not applicable / None — accept if body is short and ENDS with the pattern
    if len(body_after_prefix) < 250:
        # remove leading non-applicable text that might be a title remnant, then
        # check if what remains is the not_applicable phrase.
        # Common patterns in real edgartools output:
        #   "Mine Safety DisclosuresNot applicable."   (no space, after title)
        #   "Unresolved Staff Comments  None."
        #   "Changes in and Disagreements...\n\nNone."  (Apple Item 9: long title + None)
        #   "Not applicable."                          (clean)
        if _RE_NOT_APPLICABLE_HEAD.match(body_after_prefix):
            return "not_applicable"
        # Fallback: skip leading title-shape text (allow up to 200 chars to cover
        # long titles like Apple Item 9) ending with the not-applicable phrase.
        tail = re.sub(
            r"^[A-Za-z][\w\s,\-’'&/().]{0,200}?(?=Not\s+applicable\.?\s*$|None\.?\s*$)",
            "", body_after_prefix, count=1, flags=re.IGNORECASE,
        )
        if _RE_NOT_APPLICABLE_HEAD.match(tail.strip()):
            return "not_applicable"

    inc_matches = list(_RE_INCORPORATED.finditer(text))
    if inc_matches:
        neg_match = _RE_NEGATIVE_WEBSITE.search(text)
        if not _is_only_negative(inc_matches[0], neg_match):
            # 'remaining' / 'additional' / 'other' information clauses signal there
            # IS substantive content in this same item — strong partial indicator.
            # E.g. Apple 2024 Item 10's "The remaining information required by this
            # Item will be included in the ... Proxy Statement" after a substantive
            # insider-trading-policy paragraph.
            if _RE_REMAINING_INFO.search(text):
                return "partial"
            if _is_whole_item_by_reference(text, inc_matches):
                return "incorporated_by_reference"
            return "partial"

    if _RE_LOOSE_SEE.search(text) and len(text) < 600:
        return "incorporated_by_reference"

    return "extracted"


def _is_only_negative(inc_match: re.Match[str], neg_match: re.Match[str] | None) -> bool:
    """True when the inc match overlaps the negative-control phrase
    (e.g. 'websites referenced are not incorporated by reference')."""
    if neg_match is None:
        return False
    inc_span = inc_match.span()
    neg_span = neg_match.span()
    overlap = max(0, min(inc_span[1], neg_span[1]) - max(inc_span[0], neg_span[0]))
    return overlap > 0


def _is_whole_item_by_reference(text: str, inc_matches: list[re.Match[str]]) -> bool:
    """Heuristic: does the by-reference clause cover the bulk of the item, or is it a
    side-note in an otherwise substantive section?

    True when:
      (a) the body is very short (<400 chars) — typical pure by-ref item like
          'Item 12. Security Ownership ... incorporated herein by reference.'
      (b) OR the matched clause spans >30% of the body (density threshold)
    Items longer than 400 chars with a by-ref clause that's only a side-note
    (e.g. Apple Item 10's substantive insider-trading paragraph + trailing
    by-ref clause) fall through to `partial`.
    """
    if len(text.strip()) < 400:
        return True
    if not inc_matches:
        return False
    covered = sum(m.end() - m.start() for m in inc_matches)
    return covered / max(len(text), 1) > 0.30
