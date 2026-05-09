"""Test the bleed-trimming logic in segment._trim_bleed."""

from workers.extractor.segment import _trim_bleed


def test_no_bleed_kept_as_is():
    text = "Item 1A.    Risk Factors\n\nThe Company faces many risks..."
    assert _trim_bleed(text) == text


def test_run_on_bleed_trimmed():
    """edgartools page-break artifacts produce 'PART IIItem 5.' run-ons."""
    text = ("Item 4.\xa0\xa0\xa0\xa0Mine Safety DisclosuresNot applicable.Apple Inc. | 2024 Form 10-K | 18"
            "PART IIItem 5.\xa0\xa0\xa0\xa0Market for Registrant's Common Equity...")
    out = _trim_bleed(text)
    assert "Item 5" not in out
    assert "Market for Registrant" not in out
    assert "Mine Safety Disclosures" in out


def test_part_header_alone_kept_at_segment_layer():
    """Bare 'PART III' (without a following Item heading) is left alone here.
    the classifier's _RE_TRAILING_FOOTER handles that case downstream. We split
    the responsibility so this trim stays narrow and predictable."""
    text = ("Item 9C.\xa0\xa0\xa0\xa0Disclosure Regarding Foreign Jurisdictions"
            "Not applicable.PART III")
    out = _trim_bleed(text)
    # _trim_bleed leaves it; classifier strips it later.
    assert out == text


def test_part_header_at_start_kept():
    """If 'PART X' appears at the very beginning of an item (some filings start that way),
    we don't strip it; only trailing parts are bleed."""
    # Note: The 100-char threshold in _trim_bleed protects this; here we ensure
    # a normal item with PART in title isn't accidentally truncated.
    text = "Item 1.    Business about PART of our operations is XYZ"
    assert _trim_bleed(text) == text
