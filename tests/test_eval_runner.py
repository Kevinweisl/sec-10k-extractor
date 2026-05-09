"""Tests for the eval runner's scoring logic.

We unit-test score_filing with synthetic ExtractionResult + gold dicts so
we can assert metric correctness without hitting live SEC.
"""

from __future__ import annotations

import sys
from pathlib import Path

# evals/sec-extraction/runner.py isn't on sys.path by default
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals" / "sec-extraction"))
from runner import score_filing  # noqa: E402

from workers.extractor.schema import (  # noqa: E402
    ExtractionMeta,
    ExtractionResult,
    FilingMeta,
    Item,
)


def _build_extraction(items: list[tuple[str, str]]) -> ExtractionResult:
    """Build a minimal ExtractionResult from (item_number, status) pairs."""
    return ExtractionResult(
        filing=FilingMeta(
            cik="0", accession="X", form_type="10-K",
            filing_date="2024-01-01", period_ending="2024-01-01",
            primary_document="x.htm",
        ),
        items=[
            Item(
                part=1, item_number=n, item_title="t", status=s,
                content_text="x",
            )
            for n, s in items
        ],
        meta=ExtractionMeta(extraction_time_ms=100),
    )


def _build_gold(items: list[tuple[str, str]]) -> dict:
    return {
        "filing": {"cik": "0", "accession": "X"},
        "expected_items": [
            {"part": 1, "item_number": n, "status": s, "title_hint": "t"}
            for n, s in items
        ],
    }


def test_score_perfect_match():
    actual = _build_extraction([("1", "extracted"), ("1A", "extracted"), ("4", "not_applicable")])
    gold = _build_gold([("1", "extracted"), ("1A", "extracted"), ("4", "not_applicable")])
    s = score_filing(actual, gold)
    assert s["item_recall"] == 1.0
    assert s["item_precision"] == 1.0
    assert s["status_accuracy"] == 1.0
    assert s["full_match_rate"] == 1.0
    assert s["fp_items"] == []
    assert s["fn_items"] == []
    assert s["status_mismatches"] == []


def test_score_missing_item_lowers_recall():
    actual = _build_extraction([("1", "extracted")])  # missing 1A
    gold = _build_gold([("1", "extracted"), ("1A", "extracted")])
    s = score_filing(actual, gold)
    assert s["item_recall"] == 0.5
    assert s["item_precision"] == 1.0
    assert s["fn_items"] == ["1A"]


def test_score_extra_item_lowers_precision():
    actual = _build_extraction([("1", "extracted"), ("99", "extracted")])  # extra 99
    gold = _build_gold([("1", "extracted")])
    s = score_filing(actual, gold)
    assert s["item_recall"] == 1.0
    assert s["item_precision"] == 0.5
    assert s["fp_items"] == ["99"]


def test_score_status_mismatch():
    """Item present but classified differently; recall/prec stay 1.0,
    status_accuracy drops."""
    actual = _build_extraction([("1", "extracted"), ("11", "extracted")])
    gold = _build_gold([("1", "extracted"), ("11", "incorporated_by_reference")])
    s = score_filing(actual, gold)
    assert s["item_recall"] == 1.0
    assert s["item_precision"] == 1.0
    assert s["status_accuracy"] == 0.5  # 1 of 2 items has correct status
    assert s["full_match_rate"] == 0.5
    assert len(s["status_mismatches"]) == 1
    assert s["status_mismatches"][0]["item"] == "11"
    assert s["status_mismatches"][0]["expected"] == "incorporated_by_reference"
    assert s["status_mismatches"][0]["actual"] == "extracted"


def test_score_skips_synthetic_cover():
    """The 'cover' synthetic record is excluded from comparison."""
    actual = _build_extraction([("cover", "incorporated_by_reference"), ("1", "extracted")])
    gold = _build_gold([("1", "extracted")])
    s = score_filing(actual, gold)
    assert s["items_actual"] == 1  # cover dropped
    assert s["item_recall"] == 1.0
    assert s["item_precision"] == 1.0


def test_score_handles_zero_expected():
    """Edge case: empty gold (shouldn't happen in practice)."""
    actual = _build_extraction([])
    gold = _build_gold([])
    s = score_filing(actual, gold)
    assert s["item_recall"] == 0.0  # 0/0 returns 0 by convention
    assert s["item_precision"] == 0.0
    assert s["status_accuracy"] == 0.0


def test_score_alignment_rate():
    """Items with char_range_text count toward text_alignment_rate."""
    actual = ExtractionResult(
        filing=FilingMeta(cik="0", accession="X", form_type="10-K",
                          filing_date="2024-01-01", period_ending="2024-01-01",
                          primary_document="x.htm"),
        items=[
            Item(part=1, item_number="1", item_title="t", status="extracted",
                 content_text="x", char_range_text=(0, 100), char_range_html=(0, 200)),
            Item(part=1, item_number="1A", item_title="t", status="extracted",
                 content_text="x"),  # no ranges
        ],
        meta=ExtractionMeta(),
    )
    gold = _build_gold([("1", "extracted"), ("1A", "extracted")])
    s = score_filing(actual, gold)
    assert s["text_alignment_rate"] == 0.5
    assert s["html_alignment_rate"] == 0.5
