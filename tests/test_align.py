"""Tests for char-offset alignment.

The function must:
  - return exact range on identical text
  - tolerate whitespace differences (e.g. "(a)Documents" vs "(a) Documents")
  - return None when no good match exists
  - honor min_start to enforce sequential document ordering
"""

from workers.extractor.align import align_to_source
from workers.extractor.iou import compute_iou


def test_align_identical_substring():
    source = "BEGIN " + "x" * 200 + " The quick brown fox jumps over the lazy dog and continues its journey through the verdant fields with purpose and grace. END" + " " * 200 + "TAIL"
    segment = "The quick brown fox jumps over the lazy dog and continues its journey through the verdant fields with purpose and grace."
    span = align_to_source(segment, source)
    assert span is not None
    iou = compute_iou(span, (source.find(segment), source.find(segment) + len(segment)))
    assert iou > 0.95


def test_align_returns_none_for_no_match():
    source = "completely unrelated text " * 50
    segment = "This sentence does not appear anywhere in the source." * 4
    assert align_to_source(segment, source) is None


def test_align_returns_none_for_empty_inputs():
    assert align_to_source("", "abc") is None
    assert align_to_source("abc", "") is None


def test_align_whitespace_tolerant():
    """edgartools sometimes drops a space — '(a)Documents' in segment vs
    '(a) Documents' in source. The whitespace-tolerant fallback should catch it."""
    source = "X" * 500 + " (a) Documents filed as part of this report including the annual exhibit index for fiscal year 2024 with all required schedules. " + "Y" * 200
    segment = "Item 15.\n(a)Documents filed as part of this report including the annual exhibit index for fiscal year 2024 with all required schedules."
    span = align_to_source(segment, source)
    assert span is not None
    # the matched body should overlap the source's body region
    body_start = source.find("(a) Documents")
    assert span[0] <= body_start <= span[1]


def test_align_skips_leading_header():
    """The 'Item N. Title' prefix in segment shouldn't pollute the fingerprint."""
    source = (
        "STUFF " * 50
        + " Some other Item 7. Discussion of stuff. STUFF "
        + "PADDING " * 30
        + " The substantive risk factor analysis follows here with detail and color and historical context that uniquely identifies this section. "
        + " TAIL " * 100
    )
    segment = (
        "Item 1A. Risk Factors\n\n"
        "The substantive risk factor analysis follows here with detail and color "
        "and historical context that uniquely identifies this section."
    )
    span = align_to_source(segment, source)
    assert span is not None
    body_start = source.find("The substantive risk")
    # The returned span should start at (or near) the body, not at 'Item 7' header
    assert abs(span[0] - body_start) < 100


def test_align_min_start_enforces_sequential():
    """If a fingerprint has two matches and we search past the first one,
    we should find the second."""
    body = "Boilerplate that appears in two places exactly. " * 4
    source = body + " UNIQUE-MIDDLE-MARKER " + body + " TAIL"
    segment = "Item 11.\n" + body
    # without constraint — finds first occurrence
    s1 = align_to_source(segment, source)
    assert s1 is not None
    # with constraint — must find the second occurrence
    s2 = align_to_source(segment, source, min_start=s1[1] + 10)
    assert s2 is not None
    assert s2[0] > s1[0]


def test_align_min_start_returns_none_if_past_end():
    source = "Some content here that is finite in length."
    segment = "Some content here"
    assert align_to_source(segment, source, min_start=len(source) + 100) is None
