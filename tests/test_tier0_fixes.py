"""Unit tests for the 2026-05-28 Tier-0 fixes addressing Citi/Intel failures.

Three things being verified:
  1. is_toc_stub() correctly identifies TOC-stub content_text
  2. toc_stub_rate aggregates correctly
  3. Reserved + footnote (a/d+/symbol) classifier boundaries
  4. extract_by_page_anchors recovers bodies from anchored HTML
  5. _compute_filing_status branches as designed
  6. _build_sanity_warnings emits hard_failure for zero items
"""

from __future__ import annotations

import re

import pytest

from workers.extractor.classifier import classify_status
from workers.extractor.page_anchored import (
    extract_by_page_anchors,
    is_toc_stub,
    toc_stub_rate,
)
from workers.extractor.pipeline import (
    MIN_EXPECTED_ITEMS_FOR_HEALTHY,
    _avg_content_len,
    _build_sanity_warnings,
    _compute_filing_status,
)
from workers.extractor.regex_segment import _classify_toc_page_ref


# ============================================================================
# is_toc_stub
# ============================================================================

class TestIsTocStub:
    @pytest.mark.parametrize("content, expected", [
        # Cross-reference TOC prefix marker — definite stub
        ("[Cross-reference TOC] Business: ()", True),
        ("[Cross-reference TOC] Risk Factors (Pages 37 - 51)", True),
        ("[Cross-reference TOC] [Reserved] ()", True),
        # Empty / blank
        ("", True),
        ("   ", True),
        # Short content with page-number hint
        ("See Risk Factors at Pages 37-51 of this annual report.", True),
        # Real body — no stub
        ("Item 1A. Risk Factors. Our business is subject to a number of risks "
         "that could materially affect our results and financial condition. "
         + "More content here. " * 30, False),
        # Within-doc cross-reference — NOT a TOC stub, it's substantive
        ("See Item 13 for compensation details. The remaining information "
         "follows from our previously disclosed agreements with "
         "compensation committee under section 162(m).", False),
    ])
    def test_is_toc_stub_boundary_cases(self, content: str, expected: bool):
        assert is_toc_stub(content) is expected

    def test_none_input(self):
        assert is_toc_stub(None) is True


# ============================================================================
# toc_stub_rate
# ============================================================================

class TestTocStubRate:
    def test_empty_list_returns_zero(self):
        assert toc_stub_rate([]) == 0.0

    def test_all_stubs(self):
        items = [
            {"content_text": "[Cross-reference TOC] X (a)"},
            {"content_text": "[Cross-reference TOC] Y (Pages 5)"},
        ]
        assert toc_stub_rate(items) == 1.0

    def test_mixed_rate(self):
        items = [
            {"content_text": "[Cross-reference TOC] X (a)"},   # stub
            {"content_text": "Real body content " * 100},      # real
            {"content_text": "[Cross-reference TOC] Y (b)"},   # stub
            {"content_text": "More real content " * 100},      # real
        ]
        assert toc_stub_rate(items) == 0.5

    def test_supports_pydantic_items(self):
        class FakeItem:
            def __init__(self, ct):
                self.content_text = ct
        items = [FakeItem("[Cross-reference TOC] X ()"), FakeItem("Real body " * 100)]
        assert toc_stub_rate(items) == 0.5


# ============================================================================
# Classifier: Reserved boundary + cross-ref TOC Reserved detection
# ============================================================================

class TestClassifierBoundary:
    def test_plain_reserved_short_body(self):
        assert classify_status("Item 6. [Reserved]") == "reserved"

    def test_reserved_in_cross_ref_toc_stub(self):
        """Before fix: Intel 2026 Item 6 stub classified as 'extracted'."""
        ct = "[Cross-reference TOC] [Reserved] ()"
        assert classify_status(ct) == "reserved"

    def test_cross_ref_with_not_applicable(self):
        ct = "[Cross-reference TOC] Mine Safety Disclosures (None)"
        # Not flagged as reserved at minimum
        result = classify_status(ct)
        assert result != "reserved"


# ============================================================================
# _classify_toc_page_ref: widened footnote pattern
# ============================================================================

class TestClassifyTocPageRef:
    @pytest.mark.parametrize("page_ref, expected", [
        # Original (a)-(z) — still works
        ("(a)", "incorporated_by_reference"),
        ("(b)", "incorporated_by_reference"),
        ("(a), 5", "incorporated_by_reference"),
        # Numeric footnotes — NEW
        ("(1)", "incorporated_by_reference"),
        ("(12)", "incorporated_by_reference"),
        # Symbol footnotes — NEW
        ("(*)", "incorporated_by_reference"),
        ("(†)", "incorporated_by_reference"),
        ("(‡)", "incorporated_by_reference"),
        # Reserved — NEW (was previously slipping past)
        ("[Reserved]", "reserved"),
        # Standard cases unchanged
        ("None", "not_applicable"),
        ("Not applicable", "not_applicable"),
        ("Pages 5 - 43", "extracted"),
        ("Page 109", "extracted"),
    ])
    def test_widened_pattern(self, page_ref: str, expected: str):
        assert _classify_toc_page_ref(page_ref) == expected


# ============================================================================
# Filing-level sanity check
# ============================================================================

class FakeItem:
    """Minimal stand-in for the Item pydantic model in tests."""

    def __init__(self, item_number: str = "1", content_text: str = "body"):
        self.item_number = item_number
        self.content_text = content_text


class TestComputeFilingStatus:
    def test_abs_short_circuits(self):
        assert _compute_filing_status([], is_abs=True, stub_rate=0.0) == "abs_placeholder"

    def test_zero_items_when_not_abs(self):
        assert _compute_filing_status([], is_abs=False, stub_rate=0.0) == "extraction_failed"

    def test_low_count_marked_partial(self):
        items = [FakeItem(str(i)) for i in range(5)]  # < threshold of 8
        assert _compute_filing_status(items, is_abs=False, stub_rate=0.0) == "partial"

    def test_high_stub_rate_marked_partial(self):
        items = [FakeItem(str(i)) for i in range(20)]
        assert _compute_filing_status(items, is_abs=False, stub_rate=0.5) == "partial"

    def test_healthy_extraction(self):
        items = [FakeItem(str(i)) for i in range(20)]
        assert _compute_filing_status(items, is_abs=False, stub_rate=0.0) == "extracted"


class TestBuildSanityWarnings:
    def test_zero_items_emits_hard_failure(self):
        warns = _build_sanity_warnings([], is_abs=False, stub_rate=0.0)
        assert len(warns) == 1
        assert "zero_items_extracted [HARD_FAILURE]" in warns[0]

    def test_abs_emits_no_warnings(self):
        warns = _build_sanity_warnings([], is_abs=True, stub_rate=0.0)
        assert warns == []

    def test_low_count_emits_warning(self):
        items = [FakeItem(str(i)) for i in range(5)]
        warns = _build_sanity_warnings(items, is_abs=False, stub_rate=0.0)
        assert any("low_item_count" in w for w in warns)

    def test_high_stub_rate_emits_warning(self):
        items = [FakeItem(str(i)) for i in range(20)]
        warns = _build_sanity_warnings(items, is_abs=False, stub_rate=0.8)
        assert any("high_toc_stub_rate" in w for w in warns)

    def test_healthy_emits_no_warnings(self):
        items = [FakeItem(str(i)) for i in range(20)]
        warns = _build_sanity_warnings(items, is_abs=False, stub_rate=0.0)
        assert warns == []


# ============================================================================
# _avg_content_len helper
# ============================================================================

class TestAvgContentLen:
    def test_empty_returns_zero(self):
        assert _avg_content_len([]) == 0.0

    def test_dict_items(self):
        items = [{"content_text": "a" * 100}, {"content_text": "b" * 200}]
        assert _avg_content_len(items) == 150.0

    def test_model_items(self):
        items = [FakeItem(content_text="x" * 50), FakeItem(content_text="y" * 150)]
        assert _avg_content_len(items) == 100.0


# ============================================================================
# extract_by_page_anchors
# ============================================================================

class TestExtractByPageAnchors:
    def test_no_html_returns_none(self):
        assert extract_by_page_anchors("", []) is None

    def test_no_anchors_returns_none(self):
        html = "<html><body><p>No anchors at all.</p></body></html>"
        assert extract_by_page_anchors(html, []) is None

    def test_extracts_simple_anchored_bodies(self):
        # Synthetic HTML mimicking SEC anchor convention
        body = (
            "<html><body>"
            "<a name='item_1'></a>"
            "<h1>Item 1. Business</h1>"
            "<p>" + ("Our business overview goes here. " * 50) + "</p>"
            "<a name='item_1A'></a>"
            "<h1>Item 1A. Risk Factors</h1>"
            "<p>" + ("Risk factor body content. " * 80) + "</p>"
            "<a name='item_1B'></a>"
            "<h1>Item 1B. Unresolved Staff Comments</h1>"
            "<p>None.</p>"
            "</body></html>"
        )
        existing = [
            {"part": 1, "item_number": "1", "item_title": "Business",
             "content_text": "[Cross-reference TOC] Business ()"},
            {"part": 1, "item_number": "1A", "item_title": "Risk Factors",
             "content_text": "[Cross-reference TOC] Risk Factors (Pages 5)"},
        ]
        result = extract_by_page_anchors(body, existing)
        assert result is not None
        # Item 1 should have substantive body; Item 1B should be skipped (< 400 chars)
        item_1 = next((it for it in result if it["item_number"] == "1"), None)
        assert item_1 is not None
        assert "Our business overview" in item_1["content_text"]
        assert len(item_1["content_text"]) > 400
        item_1a = next((it for it in result if it["item_number"] == "1A"), None)
        assert item_1a is not None
        assert "Risk factor body" in item_1a["content_text"]
        # status_hint not set — let classifier decide
        assert item_1["status_hint"] is None
