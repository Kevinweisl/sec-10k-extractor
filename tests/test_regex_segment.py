"""Tests for the regex-based fallback segmenter."""

from workers.extractor.regex_segment import (
    edgartools_coverage_suspect,
    regex_segment,
)


def test_basic_segmentation():
    text = (
        "Some preamble.\n\n"
        "Item 1. Business\n\n"
        + ("Business description body. " * 30)
        + "\n\nItem 1A. Risk Factors\n\n"
        + ("Risk factor body text. " * 30)
        + "\n\nItem 7. Management Discussion\n\n"
        + ("MD&A content. " * 30)
    )
    items = regex_segment(text)
    nums = [it["item_number"] for it in items]
    assert nums == ["1", "1A", "7"]
    titles = [it["item_title"] for it in items]
    assert titles[0].startswith("Business")
    assert "Risk Factors" in titles[1]


def test_filter_toc_entries():
    """TOC entries point at body — only the body should remain."""
    text = (
        # TOC at top — items packed close together
        "TABLE OF CONTENTS\n"
        "Item 1. Business 5\n"
        "Item 1A. Risk Factors 12\n"
        "Item 7. MD&A 30\n"
        "\n\n"
        # bodies — well-separated
        "Item 1. Business\n"
        + ("Business actual content. " * 30)
        + "\n\nItem 1A. Risk Factors\n"
        + ("Risk factor actual content. " * 30)
        + "\n\nItem 7. MD&A\n"
        + ("MDA actual content. " * 30)
    )
    items = regex_segment(text)
    assert [it["item_number"] for it in items] == ["1", "1A", "7"]
    # Body content, not TOC
    assert "actual content" in items[0]["content_text"]
    assert "actual content" in items[1]["content_text"]
    assert "actual content" in items[2]["content_text"]


def test_returns_empty_for_no_headings():
    text = "Just a bunch of regular text without any item headings of any kind."
    assert regex_segment(text) == []


def test_returns_empty_for_empty():
    assert regex_segment("") == []


def test_uppercase_item_heading():
    text = (
        "ITEM 7. MANAGEMENT DISCUSSION\n\n"
        + ("body text " * 50)
        + "\n\nITEM 7A. QUANT DISCLOSURES\n\n"
        + ("more body text " * 50)
    )
    items = regex_segment(text)
    assert [it["item_number"] for it in items] == ["7", "7A"]


def test_part_assignment():
    text = (
        "Item 1. Business\n\n" + "x" * 300
        + "\n\nItem 5. Market\n\n" + "y" * 300
        + "\n\nItem 10. Directors\n\n" + "z" * 300
        + "\n\nItem 15. Exhibits\n\n" + "w" * 300
    )
    items = regex_segment(text)
    parts = {it["item_number"]: it["part"] for it in items}
    assert parts == {"1": 1, "5": 2, "10": 3, "15": 4}


# --- coverage_suspect ---

def test_coverage_suspect_few_items():
    items = [{"item_number": str(i), "content_text": "x" * 200} for i in range(1, 5)]
    assert edgartools_coverage_suspect(items) is True


def test_coverage_suspect_normal():
    items = [
        {"item_number": n, "content_text": f"unique content for {n} " * 50}
        for n in ["1", "1A", "1B", "2", "3", "4", "5", "6", "7", "7A", "8"]
    ]
    assert edgartools_coverage_suspect(items) is False


def test_coverage_suspect_duplicate_content():
    """The GE 2021 failure mode — multiple items share content."""
    same = "RISK FACTORS. " + ("identical text " * 50)
    items = [
        {"item_number": "1", "content_text": same},
        {"item_number": "1A", "content_text": same},
        {"item_number": "7", "content_text": "different MDA content " * 50},
        {"item_number": "8", "content_text": "different financials " * 50},
    ]
    # add filler so the item count alone wouldn't trigger
    for i in range(2, 7):
        items.append({"item_number": str(i), "content_text": f"filler item {i} " * 50})
    assert edgartools_coverage_suspect(items) is True
