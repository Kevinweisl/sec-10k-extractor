"""Orchestrate Phase 1 (rules-only) extraction for a single 10-K.

Pipeline:
  1. fetch.find_10k_filing(cik, accession) -> edgartools Filing
  2. segment.segment_with_edgartools(filing) -> {items, raw_text, raw_html, is_abs}
  3. for each item:
       a. classifier.classify_status(content_text) -> Status
       b. align.align_to_source(content_text, raw_text/raw_html) -> char_ranges
  4. cover_page.detect_cover_incorporates(raw_text) -> referenced filing meta
  5. era.items_applicable(filing_date, period) -> mark applicable_in_era
  6. assemble ExtractionResult and return

Phase 2 (LLM augmentation) and Phase 3 (XBRL cross-check) come on Day 3.
"""

from __future__ import annotations

import time
from datetime import date, datetime

from workers.extractor.align import align_to_source
from workers.extractor.classifier import classify_status
from workers.extractor.cover_page import detect_cover_incorporates
from workers.extractor.era import items_applicable
from workers.extractor.fetch import find_10k_filing
from workers.extractor.schema import (
    ExtractionMeta,
    ExtractionResult,
    FilingMeta,
    Item,
    ReferencedFiling,
)
from workers.extractor.segment import segment_with_edgartools


def _to_iso(d: date | datetime | str | None) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def _parse_iso_date(s: str | date | datetime | None) -> date:
    if s is None:
        return date(1900, 1, 1)
    if isinstance(s, date):
        return s
    if isinstance(s, datetime):
        return s.date()
    return date.fromisoformat(str(s)[:10])


def extract_10k(cik: str | int, accession: str) -> ExtractionResult:
    """Run Phase 1 extraction on a 10-K. No LLM calls."""
    t0 = time.perf_counter()
    filing = find_10k_filing(cik, accession)

    # Build filing-level metadata
    primary_doc = ""
    try:
        # primary_documents is a Rich-rendered list-like; use document fallback
        primary_doc = filing.document.document if filing.document else ""
    except Exception:  # noqa: BLE001
        pass

    filing_date = _parse_iso_date(filing.filing_date)
    period_ending = _parse_iso_date(filing.period_of_report)

    # Segment + classify
    seg = segment_with_edgartools(filing)
    raw_text: str = seg["raw_text"]
    raw_html: str = seg["raw_html"]
    is_abs: bool = seg["is_abs_filing"]

    # Cover page
    cover_meta = detect_cover_incorporates(raw_text)
    cover_ref = ReferencedFiling(**cover_meta) if cover_meta else None

    # What items are applicable in this filing's era?
    applicable = set(items_applicable(filing_date, period_ending))

    items: list[Item] = []
    if is_abs:
        # Single placeholder record marking this isn't a standard 10-K
        items.append(Item(
            part=0, item_number="abs",
            item_title="Asset-Backed Securities (Reg AB) filing — non-standard schema",
            status="non_standard",
            content_text="",
            applicable_in_era=False,
        ))
    else:
        for raw in seg["items"]:
            content_text: str = raw["content_text"]
            status = classify_status(content_text)
            ref: ReferencedFiling | None = None
            if status in ("incorporated_by_reference", "partial") and cover_ref:
                # All by-ref items in a typical 10-K reference the same proxy
                ref = ReferencedFiling(**cover_meta) if cover_meta else None

            text_range = align_to_source(content_text, raw_text)
            html_range = align_to_source(content_text, raw_html) if raw_html else None

            items.append(Item(
                part=raw["part"],
                item_number=raw["item_number"],
                item_title=raw["item_title"],
                status=status,
                content_text=content_text,
                char_range_text=text_range,
                char_range_html=html_range,
                applicable_in_era=raw["item_number"] in applicable,
                references=ref,
            ))

    # Synthetic cover-page record if we detected the block
    if cover_ref:
        items.insert(0, Item(
            part=0,
            item_number="cover",
            item_title="DOCUMENTS INCORPORATED BY REFERENCE (cover page)",
            status="incorporated_by_reference",
            content_text="",
            applicable_in_era=True,
            references=cover_ref,
        ))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    is_xbrl = bool(getattr(filing, "is_xbrl", False)) or bool(getattr(filing, "xbrl", None))
    meta_filing = FilingMeta(
        cik=str(int(cik)),
        accession=accession,
        form_type=filing.form,
        filing_date=_to_iso(filing.filing_date),
        period_ending=_to_iso(filing.period_of_report),
        primary_document=primary_doc or "",
        is_inline_xbrl=is_xbrl,
        is_abs_filing=is_abs,
        cover_page_incorporates=cover_ref,
    )
    return ExtractionResult(
        filing=meta_filing,
        items=items,
        meta=ExtractionMeta(extraction_time_ms=elapsed_ms),
    )
