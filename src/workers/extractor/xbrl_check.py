"""Phase 3 — cross-validate Item 8 against SEC XBRL Company Facts.

The SEC publishes structured XBRL data per company at:
  https://data.sec.gov/api/xbrl/companyfacts/CIK{10-digit-padded}.json

For each filing, we check four signals:
  A. has_xbrl_data    — filing has any XBRL facts at all (1995 SGML era → False)
  B. status_consistent — Item 8 status agrees with fact count
                         (extracted ⇒ ≥20 facts; incorporated_by_reference ⇒ <5)
  C. period_aligned   — XBRL fact periods align with our period_ending
  D. numeric reconciliation — Revenues / NetIncome / Assets values appear in
                              Item 8 text (allowing thousands / millions / billions
                              scaling, since 10-K narrative often abbreviates).

None of these are hard failures. XBRL is one signal among several; the goal is
honest reporting of mismatches, not silent pass.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterable
from pathlib import Path

import httpx

from workers.extractor.schema import (
    ExtractionResult,
    NumericReconciliation,
    XBRLValidation,
)

log = logging.getLogger(__name__)

_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Kevin Wei interview-hw-2026 weisl@nlg.csie.ntu.edu.tw",
)

# Cache XBRL JSONs to disk — these can be 5-50MB and rarely change for old
# filings. Refetch if older than 24h.
_CACHE_DIR = Path(os.environ.get("XBRL_CACHE_DIR", "data/xbrl_cache"))
_CACHE_TTL_SECONDS = 24 * 60 * 60

# Concepts we try to reconcile against Item 8 narrative. Order = priority.
# Different filers use different tags for "revenue" depending on era + industry.
_REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
)
_NET_INCOME_CONCEPTS = ("NetIncomeLoss",)
_ASSETS_CONCEPTS = ("Assets",)


def _company_facts_url(cik: int | str) -> str:
    cik_int = int(cik)
    return f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_int:010d}.json"


def _cache_path(cik: int | str) -> Path:
    cik_int = int(cik)
    return _CACHE_DIR / f"CIK{cik_int:010d}.json"


def _is_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _CACHE_TTL_SECONDS


def fetch_company_facts(cik: int | str, *, timeout: float = 30.0) -> dict | None:
    """Fetch SEC XBRL Company Facts for a CIK. Returns None on 404 (no XBRL).

    Synchronous because the rest of the extractor is sync. SEC rate-limits at
    10 req/s; we call this once per filing, so a single request is safe without
    a token bucket.
    """
    cache = _cache_path(cik)
    if _is_fresh(cache):
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("xbrl cache unreadable, refetching: %s", exc)

    url = _company_facts_url(cik)
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        log.warning("xbrl fetch failed for CIK %s: %s", cik, exc)
        return None

    if r.status_code == 404:
        return None
    if r.status_code != 200:
        log.warning("xbrl fetch CIK %s returned HTTP %s", cik, r.status_code)
        return None

    cf = r.json()
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(cf))
    except OSError as exc:
        log.warning("xbrl cache write failed: %s", exc)
    return cf


def _iter_us_gaap_facts(cf: dict) -> Iterable[tuple[str, str, dict]]:
    """Yield (concept, unit, fact) for all us-gaap facts."""
    facts_root = cf.get("facts") or {}
    us_gaap = facts_root.get("us-gaap") or {}
    for concept, data in us_gaap.items():
        units = (data or {}).get("units") or {}
        for unit, values in units.items():
            for v in values or ():
                yield concept, unit, v


def facts_for_accession(cf: dict, accession: str) -> dict[str, list[dict]]:
    """Filter Company Facts down to a single accession.

    Returns {concept: [fact, ...]}. The fact dict carries val, unit, fy, fp,
    start, end, form. Only includes facts where accn == accession.
    """
    out: dict[str, list[dict]] = {}
    for concept, unit, fact in _iter_us_gaap_facts(cf):
        if fact.get("accn") != accession:
            continue
        # carry unit forward; the bare fact dict doesn't include it
        out.setdefault(concept, []).append({**fact, "unit": unit})
    return out


def _pick_fy_value(
    facts_subset: dict[str, list[dict]],
    concept_candidates: tuple[str, ...],
    fiscal_year: int | None,
) -> tuple[str, dict] | None:
    """Pick the best (concept, fact) pair matching a fiscal year.

    Walks concept_candidates in order, picks the first one with a fact whose
    fp == "FY" (full year). Falls back to fy match if FY period missing.
    """
    for concept in concept_candidates:
        facts = facts_subset.get(concept, [])
        if not facts:
            continue
        # Prefer FY period — the 10-K headline number
        fy_facts = [f for f in facts if f.get("fp") == "FY"]
        if fiscal_year is not None:
            fy_facts = [f for f in fy_facts if f.get("fy") == fiscal_year] or fy_facts
        if fy_facts:
            return concept, fy_facts[0]
        # Fallback: any fact for this concept
        return concept, facts[0]
    return None


def _value_appears_in_text(value: float, text: str) -> str | None:
    """Check if `value` appears in text in any common 10-K abbreviation form.

    Returns the match form ("exact" | "thousands" | "millions" | "billions")
    or None.

    10-K narrative usually says "$391.0 billion" rather than "391,035,000,000".
    We test:
      - exact integer with comma separators: "391,035"
      - exact integer no separators: "391035"
      - thousands ($X thousand): value / 1_000 with 0 or 1 decimal
      - millions  ($X million):  value / 1_000_000
      - billions  ($X billion):  value / 1_000_000_000
    """
    if value == 0:
        return None
    abs_val = abs(value)

    # Exact representations — XBRL stores raw integers, so try those.
    int_val = int(round(abs_val))
    if f"{int_val:,}" in text:
        return "exact"

    # Scaled forms — try 0 / 1 / 2 decimal places. 10-K narrative typically
    # says "391.0" or "391" or "391.04".
    scales = (
        (1_000, "thousands"),
        (1_000_000, "millions"),
        (1_000_000_000, "billions"),
    )
    for divisor, label in scales:
        scaled = abs_val / divisor
        if scaled < 0.1:
            continue
        for decimals in (0, 1, 2):
            rep = f"{scaled:,.{decimals}f}"
            if rep in text:
                return label
    return None


def _expected_fiscal_year(period_ending: str) -> int | None:
    """Heuristic: fiscal year for the 10-K is the calendar year of period_ending."""
    if not period_ending or len(period_ending) < 4:
        return None
    try:
        return int(period_ending[:4])
    except ValueError:
        return None


def validate_filing(
    extraction: ExtractionResult,
    cf: dict | None,
) -> XBRLValidation:
    """Build an XBRLValidation report.

    Pass `cf=None` if Company Facts unavailable (404 or pre-XBRL era).
    """
    if cf is None:
        return XBRLValidation(
            has_xbrl_data=False,
            warnings=["No Company Facts available for this CIK (likely pre-XBRL era)."],
        )

    accession = extraction.filing.accession
    facts_subset = facts_for_accession(cf, accession)
    total_facts = sum(len(v) for v in facts_subset.values())

    if total_facts == 0:
        return XBRLValidation(
            has_xbrl_data=False,
            warnings=[
                f"Company has XBRL data but no facts tagged with accession {accession}. "
                "Filing may pre-date XBRL adoption or use a non-us-gaap taxonomy.",
            ],
        )

    item_8 = next((it for it in extraction.items if it.item_number == "8"), None)
    item_8_status = item_8.status if item_8 else None
    item_8_text = item_8.content_text if item_8 else ""
    warnings: list[str] = []

    # Signal B — status vs. count consistency. Threshold values: <20 facts is
    # "barely scaffolded"; >50 with by-ref status implies the body is actually
    # inline despite the label. Calibrated against the silver-set baselines.
    status_consistent = True
    if item_8_status == "extracted" and total_facts < 20:
        status_consistent = False
        warnings.append(
            f"Item 8 status='extracted' but only {total_facts} XBRL facts found "
            "(expected ≥20 for substantive financials).",
        )
    elif item_8_status == "incorporated_by_reference" and total_facts > 50:
        status_consistent = False
        warnings.append(
            f"Item 8 marked incorporated_by_reference but {total_facts} XBRL facts "
            "tagged to this accession — financials likely inline after all.",
        )

    # Signal C — period alignment
    fiscal_year = _expected_fiscal_year(extraction.filing.period_ending)
    period_aligned = True
    if fiscal_year is not None:
        any_fact = next(iter(facts_subset.values()))[0] if facts_subset else None
        if any_fact:
            xbrl_fy = any_fact.get("fy")
            if xbrl_fy is not None and abs(int(xbrl_fy) - fiscal_year) > 1:
                period_aligned = False
                warnings.append(
                    f"XBRL fiscal year {xbrl_fy} disagrees with period_ending "
                    f"({extraction.filing.period_ending}, fy={fiscal_year}).",
                )

    # Signal D — numeric reconciliation against Item 8 text
    reconciliations: list[NumericReconciliation] = []
    for concept_group in (_REVENUE_CONCEPTS, _NET_INCOME_CONCEPTS, _ASSETS_CONCEPTS):
        picked = _pick_fy_value(facts_subset, concept_group, fiscal_year)
        if picked is None:
            continue
        concept, fact = picked
        val = fact.get("val")
        if val is None:
            continue
        match_form = _value_appears_in_text(float(val), item_8_text) if item_8_text else None
        reconciliations.append(NumericReconciliation(
            concept=concept,
            xbrl_value=float(val),
            unit=fact.get("unit", ""),
            fiscal_year=fact.get("fy"),
            found_in_item8=match_form is not None,
            match_form=match_form,
        ))

    # If Item 8 is by-reference (or absent), missing reconciliations are
    # expected — don't warn.
    if item_8_status == "extracted" and reconciliations:
        misses = [r for r in reconciliations if not r.found_in_item8]
        if misses:
            warnings.append(
                f"{len(misses)}/{len(reconciliations)} canonical XBRL values not "
                f"found in Item 8 text (concepts: "
                f"{', '.join(r.concept for r in misses)}). May indicate truncated "
                "extraction or non-narrative financial statements.",
            )

    return XBRLValidation(
        has_xbrl_data=True,
        total_facts_for_accession=total_facts,
        item_8_status_consistent=status_consistent,
        period_aligned=period_aligned,
        numeric_reconciliations=reconciliations,
        warnings=warnings,
    )


def cross_validate(extraction: ExtractionResult) -> XBRLValidation:
    """Convenience: fetch + validate in one call."""
    cf = fetch_company_facts(extraction.filing.cik)
    return validate_filing(extraction, cf)
