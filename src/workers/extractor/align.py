"""Align segment text back to source HTML/text to compute char_range.

edgartools doesn't expose source offsets, so we use difflib to find the
longest matching block between (segment, source) and report span estimates.

Spec-required field: `char_range`. We expose two — `char_range_text` (for
the cleaned plain text) and `char_range_html` (for the raw HTML). Both are
optional in the schema; we return None when match confidence is below a
threshold rather than guessing.
"""

from __future__ import annotations

from difflib import SequenceMatcher

# Use the first ~120 chars of the segment as a fingerprint to locate it.
# Longer fingerprints cost more (SequenceMatcher is O(N*M)); shorter fingerprints
# get spurious matches. 120 was empirically OK on Apple 2024.
_FINGERPRINT_LEN = 120
_MIN_CONFIDENCE_RATIO = 0.5


def align_to_source(segment_text: str, source: str) -> tuple[int, int] | None:
    """Return (start, end) char offsets of segment_text within source, or None
    if confidence is too low.

    Strategy:
      1. Take the first 120 chars of the segment as a fingerprint (skipping
         leading 'Item N. Title' header which often appears in BOTH segment
         and source headers and creates confusion).
      2. Use SequenceMatcher.find_longest_match to locate the fingerprint.
      3. Extend the span to (start, start + len(segment_text)) capped at len(source).
      4. Return None if the matched block is < ratio of fingerprint length.
    """
    if not segment_text or not source:
        return None
    fingerprint = _build_fingerprint(segment_text)
    if not fingerprint:
        return None
    sm = SequenceMatcher(a=source, b=fingerprint, autojunk=False)
    match = sm.find_longest_match(0, len(source), 0, len(fingerprint))
    if match.size < int(_MIN_CONFIDENCE_RATIO * len(fingerprint)):
        return None
    # `match.a` is start in source; `match.b` is start in fingerprint.
    # Adjust for the fingerprint's offset within segment_text.
    fingerprint_offset = segment_text.find(fingerprint)
    if fingerprint_offset < 0:
        fingerprint_offset = 0
    start = max(0, match.a - match.b - fingerprint_offset)
    end = min(len(source), start + len(segment_text))
    return (start, end)


def _build_fingerprint(segment_text: str) -> str:
    """Skip the leading 'Item N(letter). Title' so the fingerprint contains
    real content (not header text that may appear in many places)."""
    text = segment_text.replace("\xa0", " ").strip()
    # try to skip past the title line
    nl_idx = text.find("\n")
    if nl_idx > 0 and nl_idx < 200:
        # one-line header followed by content
        body = text[nl_idx + 1:].strip()
    else:
        # no clear header break — try to skip past the first double-space group
        # (edgartools uses these as separators)
        ds_idx = text.find("  ", 50)
        if 0 < ds_idx < 200:
            body = text[ds_idx + 2:].strip()
        else:
            body = text
    if len(body) > _FINGERPRINT_LEN:
        body = body[:_FINGERPRINT_LEN]
    return body
