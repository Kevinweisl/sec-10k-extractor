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

import logging
import re
import time

from shared.dates import parse_iso_date, to_iso_string
from workers.extractor.align import align_to_source
from workers.extractor.classifier import classify_status
from workers.extractor.cover_page import detect_cover_incorporates
from workers.extractor.era import items_applicable
from workers.extractor.fetch import find_10k_filing
from workers.extractor.page_anchored import (
    extract_by_page_anchors,
    extract_by_title_headings,
    is_toc_stub,
    toc_stub_rate,
)
from workers.extractor.schema import (
    ExtractionMeta,
    ExtractionResult,
    FilingMeta,
    Item,
)
from workers.extractor.segment import segment_with_edgartools
from workers.extractor.xbrl_check import fetch_company_facts, validate_filing

log = logging.getLogger(__name__)

_ITEM_SORT_RX = re.compile(r"(\d+)([A-Za-z]?)")

# Sanity-check thresholds. A canonical 10-K should yield at least 8 items
# (Items 1, 1A, 2, 3, 4, 5, 7, 8 at minimum); modern filings yield 16-23.
MIN_EXPECTED_ITEMS_FOR_HEALTHY = 8
TOC_STUB_FALLBACK_THRESHOLD = 0.5
PAGE_ANCHORED_MIN_IMPROVEMENT = 3.0  # fallback avg content must be 3x better to swap


def _item_sort_key(seg: dict) -> tuple[int, int, str]:
    """Sort segments by document order: (part, item_num, item_letter).

    "1" < "1A" < "1B" < "2" < ... so items in Part I come out as 1, 1A, 1B, 1C,
    2, 3, 4 even though edgartools may yield them in a different order.
    """
    part = int(seg.get("part", 99))
    item_num = str(seg.get("item_number", ""))
    m = _ITEM_SORT_RX.match(item_num)
    if m:
        return (part, int(m.group(1)), m.group(2).upper())
    return (part, 999, item_num)


def extract_10k(
    cik: str | int,
    accession: str,
    *,
    xbrl_validate: bool = True,
    enable_llm_aug: bool = False,
) -> ExtractionResult:
    """Run Phase 1 (rules) + optional Phase 2 (LLM aug) + Phase 3 (XBRL) extraction.

    Args:
      xbrl_validate: fetch and cross-check SEC XBRL Company Facts (default on,
        adds one network call per filing).
      enable_llm_aug: run Phase 2 LLM ensemble vote on items where Phase 1
        looks uncertain. Default off; keeps the deterministic path and the
        existing eval baseline. Turn on per-run via the eval runner flag.
    """
    t0 = time.perf_counter()
    filing = find_10k_filing(cik, accession)

    primary_doc = ""
    try:
        primary_doc = filing.document.document if filing.document else ""
    except Exception as exc:  # noqa: BLE001
        # primary_doc lookup is best-effort; warn rather than crash if
        # edgartools' document accessor changes shape.
        log.warning("primary_doc lookup failed: %s", type(exc).__name__)

    filing_date = parse_iso_date(filing.filing_date)
    period_ending = parse_iso_date(filing.period_of_report)

    # Segment + classify
    seg = segment_with_edgartools(filing)
    raw_text: str = seg["raw_text"]
    raw_html: str = seg["raw_html"]
    is_abs: bool = seg["is_abs_filing"]

    cover_ref = detect_cover_incorporates(raw_text)

    # ---- Tier-0 fix: TOC stub fallback ------------------------------------
    # Pre-pivot bug: cross_reference_toc_segment returned items where
    # content_text was just "[Cross-reference TOC] Title (Pages N)". Two
    # recovery strategies tried in order:
    #   1. page_anchored  -- old SEC <a name="item_*"> anchors. Legacy filings.
    #   2. title_headings -- standardized SEC titles like "Risk Factors",
    #      "Properties". Modern iXBRL (Intel 2022+, etc.).
    # We swap items only when the alternate path materially improves
    # average content length (3x baseline).
    seg_items = seg["items"]
    pre_stub_rate = toc_stub_rate(seg_items)
    fallback_used: str | None = None

    def _try_fallback(
        candidate: list[dict] | None, label: str,
    ) -> bool:
        nonlocal seg_items, fallback_used
        if not candidate:
            return False
        seg_avg = _avg_content_len(seg_items)
        cand_avg = _avg_content_len(candidate)
        if cand_avg > max(seg_avg, 1) * PAGE_ANCHORED_MIN_IMPROVEMENT:
            seg_items = candidate
            fallback_used = label
            return True
        return False

    if not is_abs and pre_stub_rate > TOC_STUB_FALLBACK_THRESHOLD:
        # Strategy 1 - page anchors (legacy).
        if raw_html and not _try_fallback(
            extract_by_page_anchors(raw_html, seg_items),
            "page_anchored_after_toc_detected",
        ):
            # Strategy 2 - title headings (modern iXBRL).
            if raw_text:
                _try_fallback(
                    extract_by_title_headings(raw_text, seg_items),
                    "title_headings_after_toc_detected",
                )
    # Only recompute when a fallback actually swapped the items list.
    post_stub_rate = toc_stub_rate(seg_items) if fallback_used else pre_stub_rate

    # What items are applicable in this filing's era?
    applicable = set(items_applicable(filing_date, period_ending))

    items: list[Item] = []
    if is_abs:
        # Single placeholder record marking this isn't a standard 10-K
        items.append(Item(
            part=0, item_number="abs",
            item_title="Asset-Backed Securities (Reg AB) filing; non-standard schema",
            status="non_standard",
            content_text="",
            applicable_in_era=False,
        ))
    else:
        # Sort by (part, item_number) and thread a forward-only cursor so that
        # by-ref boilerplate collisions (Items 11-14 share near-identical text)
        # don't all resolve to the same offset.
        sorted_segs = sorted(seg_items, key=_item_sort_key)
        text_cursor = 0
        html_cursor = 0
        for raw in sorted_segs:
            content_text: str = raw["content_text"]
            hint = raw.get("status_hint")
            status = hint if hint else classify_status(content_text)
            ref = cover_ref if status in ("incorporated_by_reference", "partial") else None

            text_range = align_to_source(content_text, raw_text, min_start=text_cursor)
            html_range = align_to_source(content_text, raw_html, min_start=html_cursor) if raw_html else None

            # Only advance cursor on successful match; failed matches leave
            # cursor where it was so the next item retries from the same baseline.
            if text_range is not None:
                text_cursor = text_range[1]
            if html_range is not None:
                html_cursor = html_range[1]

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
        filing_date=to_iso_string(filing.filing_date),
        period_ending=to_iso_string(filing.period_of_report),
        primary_document=primary_doc or "",
        is_inline_xbrl=is_xbrl,
        is_abs_filing=is_abs,
        cover_page_incorporates=cover_ref,
    )
    # Phase 2; LLM augmentation (synchronous wrapper around async vote)
    aug_warnings: list[str] = []
    if enable_llm_aug and not is_abs:
        items, aug_warnings = _run_llm_augmentation(items)

    # ---- Tier-0 fix: filing-level sanity check ---------------------------
    # Before this fix, Citi 2026 returned 200 OK + items=[] + warnings=[]
    # (silent failure). Now any sub-canonical output sets explicit health
    # flags so API consumers can branch on filing_status, not just HTTP 200.
    filing_status = _compute_filing_status(items, is_abs, post_stub_rate)
    sanity_warnings = _build_sanity_warnings(items, is_abs, post_stub_rate)

    result = ExtractionResult(
        filing=meta_filing,
        items=items,
        meta=ExtractionMeta(
            extraction_time_ms=elapsed_ms,
            warnings=aug_warnings + sanity_warnings,
            filing_status=filing_status,
            toc_stub_rate=post_stub_rate,
            fallback_used=fallback_used,
        ),
    )

    # Phase 3; XBRL Company Facts cross-validation
    if xbrl_validate and not is_abs:
        cf = fetch_company_facts(cik)
        result.xbrl_validation = validate_filing(result, cf)

    return result


def _avg_content_len(items: list) -> float:
    """Average content_text length across items (0 if empty)."""
    if not items:
        return 0.0
    total = 0
    for it in items:
        ct = it.get("content_text") if isinstance(it, dict) else getattr(it, "content_text", "")
        total += len(ct or "")
    return total / len(items)


def _compute_filing_status(items: list[Item], is_abs: bool, stub_rate: float) -> str:
    """Return one of: extracted | partial | extraction_failed | abs_placeholder."""
    if is_abs:
        return "abs_placeholder"
    if len(items) == 0:
        return "extraction_failed"
    if len(items) < MIN_EXPECTED_ITEMS_FOR_HEALTHY or stub_rate > 0.3:
        return "partial"
    return "extracted"


def _build_sanity_warnings(items: list[Item], is_abs: bool, stub_rate: float) -> list[str]:
    """Emit explicit warnings for sub-canonical output (was silent before)."""
    warnings: list[str] = []
    if is_abs:
        return warnings
    if len(items) == 0:
        warnings.append(
            "zero_items_extracted [HARD_FAILURE]: edgartools + regex + page-anchored "
            "all returned no items. Filing format is outside current coverage. "
            "Verify accession; if format is novel, add to eval set + new segmenter path."
        )
        return warnings
    if len(items) < MIN_EXPECTED_ITEMS_FOR_HEALTHY:
        warnings.append(
            f"low_item_count [WARNING]: got {len(items)} items, expected >= "
            f"{MIN_EXPECTED_ITEMS_FOR_HEALTHY}. Partial extraction likely."
        )
    if stub_rate > 0.3:
        warnings.append(
            f"high_toc_stub_rate [WARNING]: {stub_rate:.0%} of items are TOC "
            "stubs (content_text is just the index line, not the body). "
            "Page-anchored fallback did not improve content."
        )
    return warnings


def _run_llm_augmentation(items: list[Item]) -> tuple[list[Item], list[str]]:
    """For each item that triggers should_augment_status, run the K-vote
    ensemble and apply the override if confidence is high enough.

    Sync wrapper; uses asyncio.run on a fresh event loop, so this can be
    called from sync code paths. For high-throughput batch jobs the eval
    runner can do its own async fan-out instead.
    """
    import asyncio

    from workers.extractor.llm_assist import (
        augment_status,
        should_augment_status,
        should_override_phase1,
    )

    # Collect (index, item, trigger_reason) for items that need augmentation.
    targets = []
    for i, it in enumerate(items):
        reason = should_augment_status(it.status, it.content_text)
        if reason:
            targets.append((i, it, reason))

    if not targets:
        return items, []

    async def run_all():
        coros = [
            augment_status(
                item_number=it.item_number,
                item_title=it.item_title,
                content_text=it.content_text,
                phase1_status=it.status,
                trigger_reason=reason,
            )
            for _, it, reason in targets
        ]
        return await asyncio.gather(*coros, return_exceptions=True)

    try:
        results = asyncio.run(run_all())
    except RuntimeError as exc:
        # Re-raise unless this is the specific "asyncio.run from inside a
        # running loop" case; silently swallowing every RuntimeError would
        # hide real LLM-vote bugs as if they were event-loop conflicts.
        if "running event loop" not in str(exc):
            raise
        return items, ["LLM augmentation skipped: running event loop already active"]

    warnings: list[str] = []
    new_items = list(items)
    for (idx, item, reason), vote in zip(targets, results, strict=True):
        if isinstance(vote, BaseException):
            warnings.append(
                f"Item {item.item_number}: LLM aug failed ({type(vote).__name__}: {vote})"
            )
            continue
        if should_override_phase1(vote, item.status):
            warnings.append(
                f"Item {item.item_number}: status {item.status} -> {vote.pick} "
                f"(LLM K-vote, confidence={vote.confidence:.2f}, trigger={reason!r})"
            )
            new_items[idx] = item.model_copy(update={"status": vote.pick})
        else:
            warnings.append(
                f"Item {item.item_number}: Phase 1 status={item.status} kept "
                f"(LLM vote pick={vote.pick}, confidence={vote.confidence:.2f}, "
                f"trigger={reason!r})"
            )
    return new_items, warnings
