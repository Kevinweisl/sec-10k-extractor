"""Align segment text back to source HTML/text to compute char_range.

edgartools doesn't expose source offsets, so we use difflib + whitespace-tolerant
regex fallback to find each segment's span within the source.

Spec-required field: `char_range`. We expose two; `char_range_text` (cleaned
plain text) and `char_range_html` (raw HTML). Both are optional; we return None
when match confidence is too low rather than guessing.

Robustness layers (each is a fallback for the previous failing):
  1. SequenceMatcher on a 120-char fingerprint from the segment body.
  2. Whitespace-tolerant regex on the same fingerprint (handles cases like
     edgartools outputting "(a)Documents" where source has "(a) Documents").
  3. Two more fingerprints from offsets 200 and 500 of the segment body.
     in case the head fingerprint hits a section that's been collapsed.

Sequential constraint:
  Items in a 10-K appear in document order. We accept a `min_start` argument
  so callers can thread "search after the previous match's end", which fixes
  the by-reference-boilerplate collision (Items 11-14 all use near-identical
  text and a naive matcher will resolve all four to the same offset).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# Use the first ~120 chars of the segment as a fingerprint to locate it.
# Longer fingerprints cost more (SequenceMatcher is O(N*M)); shorter fingerprints
# get spurious matches. 120 was empirically OK on Apple 2024.
_FINGERPRINT_LEN = 120
_MIN_CONFIDENCE_RATIO = 0.5

# Multiple body offsets to try as fingerprints; later offsets help when the
# head of an item is generic boilerplate that appears elsewhere in the doc.
_FINGERPRINT_OFFSETS = (0, 200, 500)


def align_to_source(
    segment_text: str,
    source: str,
    *,
    min_start: int = 0,
) -> tuple[int, int] | None:
    """Return (start, end) char offsets of segment_text within source[min_start:],
    or None if no fingerprint reaches the confidence threshold.

    `min_start` lets the caller enforce sequential-document ordering; pass the
    previous successful match's end position to avoid two adjacent items
    resolving to the same boilerplate snippet.
    """
    if not segment_text or not source:
        return None
    sub = source[min_start:]
    if not sub:
        return None

    body = _strip_leading_header(segment_text)
    if not body:
        return None

    # Try multiple fingerprint offsets; pick the highest-confidence match.
    best: tuple[int, int, int] | None = None  # (confidence, start, end)
    for offset in _FINGERPRINT_OFFSETS:
        fp = body[offset:offset + _FINGERPRINT_LEN]
        if len(fp) < 40:
            # too short to be uniquely identifying
            continue

        # Layer 1; exact-character SequenceMatcher
        match = _seq_match(sub, fp)
        if match is not None:
            confidence, m_start = match
            start = max(0, m_start - offset - _header_offset(segment_text, body))
            end = min(len(sub), start + len(segment_text))
            candidate = (confidence, min_start + start, min_start + end)
            if best is None or candidate[0] > best[0]:
                best = candidate
            if confidence >= len(fp):
                # exact match; no need to keep searching
                break

        # Layer 2; whitespace-tolerant regex
        m = _whitespace_tolerant_search(sub, fp)
        if m is not None:
            m_start = m.start()
            # whitespace-tolerant matches are slightly less precise, score
            # them as 80% of fp length so exact matches still win.
            confidence = int(0.8 * len(fp))
            start = max(0, m_start - offset - _header_offset(segment_text, body))
            end = min(len(sub), start + len(segment_text))
            candidate = (confidence, min_start + start, min_start + end)
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None:
        return None
    return (best[1], best[2])


def _strip_leading_header(segment_text: str) -> str:
    """Skip past 'Item N. Title' header so the fingerprint contains real content
    rather than header text that may appear in many places."""
    text = segment_text.replace("\xa0", " ").strip()
    nl_idx = text.find("\n")
    if 0 < nl_idx < 200:
        return text[nl_idx + 1:].strip()
    # fall back: skip past first run of consecutive spaces (edgartools separator)
    ds_idx = text.find("  ", 50)
    if 0 < ds_idx < 200:
        return text[ds_idx + 2:].strip()
    return text


def _header_offset(original: str, body: str) -> int:
    """How many chars of the original were the header (so we can subtract
    when computing the segment's start position from the body's match)."""
    if not body:
        return 0
    idx = original.find(body[:40])
    return idx if idx > 0 else 0


def _seq_match(source: str, fingerprint: str) -> tuple[int, int] | None:
    """Locate fingerprint inside source. Returns (matched_length, start_in_source).

    Fast path: literal `str.find` (microseconds on MB-sized source). Most
    Phase-1 segments come straight from edgartools and the fingerprint is
    a literal substring of the source; no fuzzy match needed.

    Fallback: SequenceMatcher (O(N*M); seconds on MB-sized source) for
    segments where edgartools normalised whitespace or unicode away from
    the source representation.
    """
    idx = source.find(fingerprint)
    if idx >= 0:
        return len(fingerprint), idx

    sm = SequenceMatcher(a=source, b=fingerprint, autojunk=False)
    match = sm.find_longest_match(0, len(source), 0, len(fingerprint))
    if match.size < int(_MIN_CONFIDENCE_RATIO * len(fingerprint)):
        return None
    # match.a = position in source; match.b = position within fingerprint.
    # If match.b > 0, the matched chunk starts mid-fingerprint, so adjust back.
    return match.size, max(0, match.a - match.b)


def _whitespace_tolerant_search(source: str, fingerprint: str) -> re.Match[str] | None:
    """Match fingerprint against source ignoring whitespace differences.

    Tokens (\\S+ runs) must match exactly; whitespace between them is allowed
    to be any whitespace sequence. This handles edgartools quirks like
    'foo' vs 'foo' with extra newlines.
    """
    tokens = fingerprint.split()
    if len(tokens) < 4:
        # too few tokens to be unique
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens[:20])  # cap to keep regex fast
    try:
        return re.search(pattern, source)
    except re.error:
        return None
