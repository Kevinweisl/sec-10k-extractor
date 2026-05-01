"""Silver-set runner — coverage and sanity checks on 7 unannotated filings.

Silver filings don't have hand-validated `expected_items` lists. Instead,
each filing has `expected` constraints: minimum item count, must-have items,
must-NOT-have items (era boundaries), and structural flags (is_abs).

Reports per filing:
  - total items found, distribution of statuses
  - era applicability check (items expected to be present in era are present)
  - constraint violations (e.g. min_items not met, must_not_have appears)
  - LLM-aug warnings (which items got Phase 2 overrides, with confidence)
  - extraction time

Usage:
    python evals/sec-extraction/silver_runner.py
    python evals/sec-extraction/silver_runner.py --with-llm
    python evals/sec-extraction/silver_runner.py --filing intel-2022
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from workers.extractor.era import items_applicable, part_for_item  # noqa: E402
from workers.extractor.pipeline import extract_10k  # noqa: E402
from datetime import date  # noqa: E402


def _parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])


def evaluate(filing_spec: dict, *, with_xbrl: bool, with_llm: bool) -> dict:
    cik = filing_spec["cik"]
    accession = filing_spec["accession"]
    expected = filing_spec.get("expected", {})

    t0 = time.perf_counter()
    error = None
    try:
        result = extract_10k(
            cik, accession,
            xbrl_validate=with_xbrl,
            enable_llm_aug=with_llm,
        )
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        return {
            "key": filing_spec["key"],
            "error": error,
            "wall_time_ms": int((time.perf_counter() - t0) * 1000),
        }
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    items = [it for it in result.items if it.item_number != "cover"]
    item_nums = [it.item_number for it in items]
    statuses = {it.item_number: it.status for it in items}
    status_dist = {}
    for it in items:
        status_dist[it.status] = status_dist.get(it.status, 0) + 1

    # Era applicability check
    period = _parse_date(result.filing.period_ending)
    filing_dt = _parse_date(result.filing.filing_date)
    era_applicable = set(items_applicable(filing_dt, period))

    found_set = set(item_nums)
    expected_present_in_era = era_applicable - {"cover", "abs"}
    missing_from_era = expected_present_in_era - found_set
    unexpected_in_era = found_set - expected_present_in_era - {"abs"}

    # Constraint checks
    violations: list[str] = []
    if "min_items" in expected and len(items) < expected["min_items"]:
        violations.append(f"min_items={expected['min_items']} but got {len(items)}")
    if "max_items" in expected and len(items) > expected["max_items"]:
        violations.append(f"max_items={expected['max_items']} but got {len(items)}")
    if "must_not_have" in expected:
        for n in expected["must_not_have"]:
            if n in found_set:
                violations.append(f"unexpected item {n} present (era boundary)")
    if "must_have_status" in expected:
        for n, allowed in expected["must_have_status"].items():
            if n not in statuses:
                violations.append(f"required item {n} missing")
            elif statuses[n] not in allowed:
                violations.append(
                    f"item {n} has status {statuses[n]!r}, expected one of {allowed}"
                )
    if "is_abs" in expected:
        actual_abs = result.filing.is_abs_filing
        if actual_abs != expected["is_abs"]:
            violations.append(
                f"is_abs={actual_abs}, expected {expected['is_abs']}"
            )

    # XBRL summary
    xbrl_summary = None
    if result.xbrl_validation is not None:
        xv = result.xbrl_validation
        xbrl_summary = {
            "has_xbrl": xv.has_xbrl_data,
            "facts": xv.total_facts_for_accession,
            "status_consistent": xv.item_8_status_consistent,
            "warnings": len(xv.warnings),
        }

    # Capture aug warnings (status overrides)
    aug_overrides = [
        w for w in result.meta.warnings
        if "->" in w and "LLM K-vote" in w
    ]
    aug_kept = [
        w for w in result.meta.warnings
        if "Phase 1 status=" in w
    ]

    return {
        "key": filing_spec["key"],
        "characteristic": filing_spec.get("characteristic", ""),
        "cik": cik,
        "accession": accession,
        "form": result.filing.form_type,
        "period_ending": result.filing.period_ending,
        "items_found": len(items),
        "item_numbers": item_nums,
        "status_distribution": status_dist,
        "missing_from_era": sorted(missing_from_era),
        "unexpected_in_era": sorted(unexpected_in_era),
        "violations": violations,
        "xbrl": xbrl_summary,
        "aug_overrides": aug_overrides,
        "aug_kept": aug_kept,
        "is_abs": result.filing.is_abs_filing,
        "wall_time_ms": elapsed_ms,
        "extraction_time_ms": result.meta.extraction_time_ms,
        "error": None,
    }


def print_report(report: list[dict], *, with_llm: bool) -> None:
    print()
    print(f"# Silver-set Eval Report ({'with' if with_llm else 'without'} LLM aug)")
    print()
    print(f"**{len(report)} filings** evaluated")
    print()
    # Compact summary table
    print("| Filing | Items | Form | Violations | Aug Overrides | Time(ms) |")
    print("|---|---|---|---|---|---|")
    for r in report:
        if r.get("error"):
            print(f"| {r['key']} | ERROR | — | {r['error']} | 0 | {r['wall_time_ms']} |")
            continue
        v = len(r["violations"])
        a = len(r["aug_overrides"])
        print(f"| {r['key']} | {r['items_found']} | {r['form']} | "
              f"{v if v else '—'} | {a if a else '—'} | {r['wall_time_ms']} |")
    print()
    # Detail per filing
    for r in report:
        if r.get("error"):
            print(f"\n## {r['key']}\nERROR: {r['error']}")
            continue
        print(f"\n## {r['key']} — {r['characteristic']}")
        print(f"- CIK={r['cik']}, accession={r['accession']}, form={r['form']}, "
              f"period={r['period_ending']}")
        print(f"- {r['items_found']} items: {', '.join(r['item_numbers'])}")
        print(f"- status distribution: {r['status_distribution']}")
        if r["missing_from_era"]:
            print(f"- missing items expected in era: {r['missing_from_era']}")
        if r["unexpected_in_era"]:
            print(f"- items present but not in era: {r['unexpected_in_era']}")
        if r["violations"]:
            print(f"- VIOLATIONS:")
            for v in r["violations"]:
                print(f"  - {v}")
        if r.get("xbrl"):
            print(f"- XBRL: {r['xbrl']}")
        if r["aug_overrides"]:
            print(f"- LLM aug overrides ({len(r['aug_overrides'])}):")
            for w in r["aug_overrides"]:
                print(f"  - {w}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--silver-spec", type=Path,
                   default=Path(__file__).resolve().parent / "silver" / "silver_filings.json")
    p.add_argument("--no-xbrl", action="store_true")
    p.add_argument("--with-llm", action="store_true")
    p.add_argument("--filing", type=str, default=None,
                   help="Run only this filing key (e.g. intel-2022)")
    p.add_argument("--out", type=Path,
                   default=Path(__file__).resolve().parent / "silver" / "last_run.json")
    args = p.parse_args()

    spec = json.loads(args.silver_spec.read_text())
    filings = spec["filings"]
    if args.filing:
        filings = [f for f in filings if f["key"] == args.filing]
        if not filings:
            print(f"no filing with key={args.filing!r}", file=sys.stderr)
            return 2

    report = []
    for f in filings:
        print(f"Running {f['key']} (CIK {f['cik']}, {f['accession']})...", flush=True)
        r = evaluate(f, with_xbrl=not args.no_xbrl, with_llm=args.with_llm)
        report.append(r)
        if r.get("error"):
            print(f"  ERROR: {r['error']}")
        else:
            v = len(r["violations"])
            print(f"  done in {r['wall_time_ms']}ms — {r['items_found']} items, "
                  f"{v} violations")

    args.out.write_text(json.dumps(report, indent=2, default=str))
    print_report(report, with_llm=args.with_llm)
    print(f"\n(JSON saved to {args.out})")

    has_violations = any(r.get("violations") for r in report)
    has_errors = any(r.get("error") for r in report)
    return 1 if (has_violations or has_errors) else 0


if __name__ == "__main__":
    sys.exit(main())
