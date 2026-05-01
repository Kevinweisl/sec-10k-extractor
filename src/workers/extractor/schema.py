"""Pydantic models for the SEC 10-K Item-level extraction output.

Schema matches the interview brief's required fields:
  part, item_number, item_title, content_text, char_range, status
plus our additions for honest data:
  applicable_in_era, references, segments, char_range_html / char_range_text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# status enum extends the brief's 4 to 6 to faithfully report mixed cases
# rather than collapsing them silently.
Status = Literal[
    "extracted",                  # substantive in-line disclosure
    "incorporated_by_reference",  # whole item points to proxy/other filing
    "not_applicable",             # explicit "Not applicable" or "None"
    "reserved",                   # "[Reserved]" (Item 6 since 2021)
    "partial",                    # mixed in-line + by-reference (e.g. Apple 2024 Item 10)
    "non_standard",               # Reg AB ABS 10-Ks etc. — schema doesn't apply
]


class ReferencedFiling(BaseModel):
    """Where a by-reference item points."""

    target_form: str = "DEF 14A"
    expected_year: int | None = None
    proxy_120_day_window: tuple[str, str] | None = None  # ISO dates
    resolved_accession: str | None = None  # filled by post-processing if time permits


class ItemSegment(BaseModel):
    """For status='partial', individual paragraphs marked as inline vs by-reference."""

    text: str
    char_range_html: tuple[int, int] | None = None
    char_range_text: tuple[int, int] | None = None
    is_incorporated_by_reference: bool = False


class Item(BaseModel):
    part: int = Field(ge=0, le=4)  # 0 = synthetic cover-page record
    item_number: str               # "1", "1A", "1B", "1C", "6", "9C", "cover"
    item_title: str
    status: Status
    content_text: str
    char_range_html: tuple[int, int] | None = None
    char_range_text: tuple[int, int] | None = None
    applicable_in_era: bool = True
    references: ReferencedFiling | None = None
    segments: list[ItemSegment] | None = None


class FilingMeta(BaseModel):
    cik: str
    accession: str
    form_type: str                       # "10-K" | "10-K/A" | "10-K405" | etc.
    filing_date: str                     # ISO date
    period_ending: str                   # ISO date
    primary_document: str                # e.g. "aapl-20240928.htm"
    is_inline_xbrl: bool = False
    is_abs_filing: bool = False          # Reg AB schema, status=non_standard
    cover_page_incorporates: ReferencedFiling | None = None


class ExtractionMeta(BaseModel):
    parser_version: str = "0.1.0"
    extraction_time_ms: int = 0
    cost_usd: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class NumericReconciliation(BaseModel):
    """One concept's XBRL value vs. whether it was found in Item 8 text."""

    concept: str                 # e.g. "us-gaap:Revenues"
    xbrl_value: float
    unit: str                    # "USD", "shares", etc.
    fiscal_year: int | None = None
    found_in_item8: bool         # whether the value appears in Item 8 content_text
    match_form: str | None = None  # "exact" | "thousands" | "millions" | "billions" | None


class XBRLValidation(BaseModel):
    """Phase 3 cross-validation against SEC XBRL Company Facts.

    None of these are hard failures — XBRL is one signal among several.
    Honest reporting means we surface mismatches, not silently pass.
    """

    has_xbrl_data: bool                       # filing has any XBRL facts at all
    total_facts_for_accession: int = 0        # facts tagged with this accession
    item_8_status_consistent: bool = True     # status vs. fact-count agreement
    period_aligned: bool = True               # XBRL period vs. our period_ending
    numeric_reconciliations: list[NumericReconciliation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    filing: FilingMeta
    items: list[Item]
    meta: ExtractionMeta
    xbrl_validation: XBRLValidation | None = None
