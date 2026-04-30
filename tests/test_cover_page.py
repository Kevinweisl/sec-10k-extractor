"""Cover-page DOCUMENTS INCORPORATED BY REFERENCE detection."""

from workers.extractor.cover_page import detect_cover_incorporates


def test_apple_2024_cover_canonical():
    text = """DOCUMENTS INCORPORATED BY REFERENCE
Portions of the Registrant's definitive proxy statement relating to its 2025
annual meeting of shareholders are incorporated by reference into Part III of
this Annual Report on Form 10-K where indicated."""
    out = detect_cover_incorporates(text)
    assert out is not None
    assert out["target_form"] == "DEF 14A"
    assert out["expected_year"] == 2025
    assert out["resolved_accession"] is None  # filled by later resolver


def test_no_cover_block_returns_none():
    assert detect_cover_incorporates("This is the regular 10-K body.\nPart I - Business...") is None


def test_only_proxy_statement_phrase_without_block_header_does_not_match():
    text = "Other registrants may also incorporate by reference into a future Proxy Statement"
    assert detect_cover_incorporates(text) is None


def test_lowercase_block_header_still_matches():
    text = "documents incorporated by reference\nPortions of the 2024 proxy statement..."
    out = detect_cover_incorporates(text)
    assert out is not None
    assert out["expected_year"] == 2024
