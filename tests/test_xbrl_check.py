"""Tests for XBRL Company Facts cross-validation.

We mock SEC Company Facts JSON rather than hit the network. The shape mirrors
the real https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json output:

  {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
      "us-gaap": {
        "Revenues": {
          "label": "Revenues",
          "units": {
            "USD": [
              {"start": "2023-10-01", "end": "2024-09-28", "val": 391035000000,
               "accn": "0000320193-24-000123", "fy": 2024, "fp": "FY",
               "form": "10-K", ...},
              ...
            ]
          }
        },
        ...
      }
    }
  }
"""

from __future__ import annotations

from workers.extractor.schema import (
    ExtractionMeta,
    ExtractionResult,
    FilingMeta,
    Item,
)
from workers.extractor.xbrl_check import (
    _value_appears_in_text,
    facts_for_accession,
    validate_filing,
)


# --- _value_appears_in_text — the trickiest part ---

def test_value_exact_with_separators():
    assert _value_appears_in_text(391035, "Revenue was $391,035 million") == "exact"


def test_value_billions_one_decimal():
    # $391.04B → text says "$391.0 billion"
    text = "Total net sales of $391.0 billion in fiscal 2024"
    assert _value_appears_in_text(391_035_000_000, text) == "billions"


def test_value_millions_no_decimal():
    text = "Net income of 93,736 million"
    assert _value_appears_in_text(93_736_000_000, text) == "millions"


def test_value_exact_full_int():
    # XBRL value as a raw integer with comma separators in narrative
    text = "Total assets of $364,980,000,000 at year end."
    assert _value_appears_in_text(364_980_000_000, text) == "exact"


def test_value_not_found():
    assert _value_appears_in_text(391_035_000_000, "Net sales were strong.") is None


def test_value_zero_returns_none():
    assert _value_appears_in_text(0, "Net income was zero") is None


def test_value_negative_loss():
    # losses (negative) — the absolute value is what appears in text
    text = "We recorded a net loss of $1.2 billion."
    assert _value_appears_in_text(-1_200_000_000, text) == "billions"


# --- facts_for_accession ---

ACCN_2024 = "0000320193-24-000123"
ACCN_OTHER = "0000320193-23-000106"


def _mock_cf() -> dict:
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"val": 391_035_000_000, "accn": ACCN_2024,
                             "fy": 2024, "fp": "FY", "form": "10-K",
                             "end": "2024-09-28"},
                            {"val": 383_285_000_000, "accn": ACCN_OTHER,
                             "fy": 2023, "fp": "FY", "form": "10-K",
                             "end": "2023-09-30"},
                        ],
                    },
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"val": 93_736_000_000, "accn": ACCN_2024,
                             "fy": 2024, "fp": "FY", "form": "10-K",
                             "end": "2024-09-28"},
                        ],
                    },
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {"val": 364_980_000_000, "accn": ACCN_2024,
                             "fy": 2024, "fp": "FY", "form": "10-K",
                             "end": "2024-09-28"},
                        ],
                    },
                },
            },
        },
    }


def test_facts_for_accession_filters_correctly():
    cf = _mock_cf()
    out = facts_for_accession(cf, ACCN_2024)
    assert "Revenues" in out
    assert "NetIncomeLoss" in out
    assert "Assets" in out
    assert len(out["Revenues"]) == 1
    assert out["Revenues"][0]["val"] == 391_035_000_000
    assert out["Revenues"][0]["unit"] == "USD"


def test_facts_for_accession_excludes_other():
    cf = _mock_cf()
    out = facts_for_accession(cf, "nonexistent")
    assert out == {}


# --- validate_filing — full report assembly ---

def _build_extraction(item_8_status: str, item_8_text: str) -> ExtractionResult:
    return ExtractionResult(
        filing=FilingMeta(
            cik="320193", accession=ACCN_2024, form_type="10-K",
            filing_date="2024-11-01", period_ending="2024-09-28",
            primary_document="aapl-20240928.htm", is_inline_xbrl=True,
        ),
        items=[Item(
            part=2, item_number="8", item_title="Financial Statements",
            status=item_8_status, content_text=item_8_text,
        )],
        meta=ExtractionMeta(),
    )


def test_validate_no_cf_returns_no_xbrl():
    result = _build_extraction("extracted", "...")
    v = validate_filing(result, cf=None)
    assert v.has_xbrl_data is False
    assert "pre-XBRL" in v.warnings[0]


def test_validate_extracted_with_matching_text():
    text = (
        "Net sales reached $391.0 billion in fiscal 2024. "
        "Net income totaled $93,736 million. "
        "Total assets stood at $365.0 billion."
    )
    result = _build_extraction("extracted", text)
    v = validate_filing(result, _mock_cf())
    assert v.has_xbrl_data is True
    assert v.total_facts_for_accession == 3
    assert v.period_aligned is True
    assert len(v.numeric_reconciliations) == 3
    found = {r.concept: r for r in v.numeric_reconciliations}
    assert found["Revenues"].found_in_item8 is True
    assert found["NetIncomeLoss"].found_in_item8 is True
    assert found["Assets"].found_in_item8 is True


def test_validate_extracted_with_low_fact_count_warns():
    # Strip CF down to 1 fact — looks like a stub
    cf = _mock_cf()
    cf["facts"]["us-gaap"] = {
        "Revenues": cf["facts"]["us-gaap"]["Revenues"],
    }
    result = _build_extraction("extracted", "Revenue was $391.0 billion.")
    v = validate_filing(result, cf)
    assert v.item_8_status_consistent is False
    assert any("only 1 XBRL facts" in w for w in v.warnings)


def test_validate_by_reference_with_facts_warns():
    # If Item 8 says by-reference but XBRL has many facts, that's inconsistent
    cf = _mock_cf()
    # Inflate fact count beyond 50 to trigger the warning branch
    base = cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]
    cf["facts"]["us-gaap"]["Revenues"]["units"]["USD"] = [
        {**base, "fp": f"Q{(i % 4) + 1}"} for i in range(60)
    ]
    result = _build_extraction("incorporated_by_reference", "")
    v = validate_filing(result, cf)
    assert v.item_8_status_consistent is False
    assert any("incorporated_by_reference" in w for w in v.warnings)


def test_validate_period_misalignment_warns():
    cf = _mock_cf()
    # Bump every fy by 5 years — should trip period_aligned check
    for concept_data in cf["facts"]["us-gaap"].values():
        for facts in concept_data["units"].values():
            for f in facts:
                f["fy"] = (f.get("fy") or 2024) + 5
    result = _build_extraction("extracted", "$391.0 billion")
    v = validate_filing(result, cf)
    assert v.period_aligned is False
    assert any("fiscal year" in w for w in v.warnings)


def test_validate_extracted_missing_numbers_warns():
    # Item 8 status is extracted but text contains none of the canonical values
    result = _build_extraction(
        "extracted",
        "This item contains the consolidated financial statements...",
    )
    v = validate_filing(result, _mock_cf())
    assert any("not found in Item 8" in w for w in v.warnings)
    assert all(not r.found_in_item8 for r in v.numeric_reconciliations)


def test_validate_by_reference_no_text_warning():
    # If by-reference, missing text values are expected — should NOT warn about that
    result = _build_extraction("incorporated_by_reference", "")
    v = validate_filing(result, _mock_cf())
    # Status warning shouldn't fire (3 facts is below the 50-fact threshold)
    assert v.item_8_status_consistent is True
    # No "not found in Item 8" warning for by-ref items
    assert not any("not found in Item 8" in w for w in v.warnings)
