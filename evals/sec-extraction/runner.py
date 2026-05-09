"""Eval runner for the SEC 10-K extractor.

For each gold filing under `evals/sec-extraction/gold/`:
  1. Run the extractor live.
  2. Score actual output against the gold's expected_items.
  3. Report per-filing recall / precision / status accuracy / mean text-IoU /
     XBRL coverage / latency.

Usage:
    python evals/sec-extraction/runner.py
    python evals/sec-extraction/runner.py --gold-dir custom/path
    python evals/sec-extraction/runner.py --no-xbrl   # skip XBRL fetch (faster)

Prints a markdown table at the end and writes a JSON artifact to
`evals/sec-extraction/last_run.json` for trend tracking.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Make src/ importable when run as a script
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Load .env so LLM augmentation finds NIM_* and EXTRACTOR_AUG_MODELS env vars.
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from workers.extractor.pipeline import extract_10k  # noqa: E402


def _normalize_item_status_pair(it: dict) -> tuple[str, str]:
    """Canonicalize for set comparison: (item_number_upper, status)."""
    return (str(it["item_number"]).upper(), it["status"])


def score_filing(actual, gold: dict) -> dict:
    """Score one filing's actual extraction against its gold annotations.

    Returns a flat dict with metrics + raw counts for trend tracking.
    """
    expected = [
        {"item_number": it["item_number"], "status": it["status"]}
        for it in gold["expected_items"]
    ]
    expected_set = {_normalize_item_status_pair(it) for it in expected}
    expected_items_set = {it["item_number"].upper() for it in expected}

    # Filter out synthetic 'cover' record from actual when comparing — the gold
    # files don't include a synthetic cover-page entry.
    actual_items = [it for it in actual.items if it.item_number != "cover"]
    actual_set = {(it.item_number.upper(), it.status) for it in actual_items}
    actual_items_set = {it.item_number.upper() for it in actual_items}

    # Item-presence recall/precision (status-agnostic — did we find the item?)
    item_tp = len(expected_items_set & actual_items_set)
    item_fp = len(actual_items_set - expected_items_set)
    item_fn = len(expected_items_set - actual_items_set)
    item_recall = item_tp / (item_tp + item_fn) if (item_tp + item_fn) else 0.0
    item_precision = item_tp / (item_tp + item_fp) if (item_tp + item_fp) else 0.0

    # Status accuracy among items present in BOTH
    common = expected_items_set & actual_items_set
    if common:
        gold_status_by_num = {
            it["item_number"].upper(): it["status"] for it in expected
        }
        actual_status_by_num = {it.item_number.upper(): it.status for it in actual_items}
        correct = sum(
            1 for n in common if gold_status_by_num[n] == actual_status_by_num[n]
        )
        status_accuracy = correct / len(common)
    else:
        status_accuracy = 0.0

    # Status full-tuple match (item_number, status)
    full_tp = len(expected_set & actual_set)
    full_match_rate = full_tp / len(expected_set) if expected_set else 0.0

    # Coverage of char ranges (we don't have hand-annotated gold ranges yet,
    # so we report alignment-rate-vs-actual-items as a self-consistency proxy
    # rather than IoU vs gold).
    text_aligned = sum(1 for it in actual_items if it.char_range_text)
    html_aligned = sum(1 for it in actual_items if it.char_range_html)
    text_align_rate = text_aligned / len(actual_items) if actual_items else 0.0
    html_align_rate = html_aligned / len(actual_items) if actual_items else 0.0

    # XBRL validation summary
    xbrl = actual.xbrl_validation
    xbrl_summary: dict = {"validated": xbrl is not None}
    if xbrl is not None:
        xbrl_summary.update({
            "has_xbrl_data": xbrl.has_xbrl_data,
            "total_facts": xbrl.total_facts_for_accession,
            "status_consistent": xbrl.item_8_status_consistent,
            "period_aligned": xbrl.period_aligned,
            "reconciliations": len(xbrl.numeric_reconciliations),
            "reconciliations_matched": sum(
                1 for r in xbrl.numeric_reconciliations if r.found_in_item8
            ),
            "warnings": len(xbrl.warnings),
        })

    return {
        "items_actual": len(actual_items),
        "items_expected": len(expected),
        "item_recall": round(item_recall, 4),
        "item_precision": round(item_precision, 4),
        "status_accuracy": round(status_accuracy, 4),
        "full_match_rate": round(full_match_rate, 4),
        "text_alignment_rate": round(text_align_rate, 4),
        "html_alignment_rate": round(html_align_rate, 4),
        "extraction_time_ms": actual.meta.extraction_time_ms,
        "xbrl": xbrl_summary,
        "fp_items": sorted(actual_items_set - expected_items_set),
        "fn_items": sorted(expected_items_set - actual_items_set),
        "status_mismatches": _status_mismatches(
            actual_items, gold["expected_items"]
        ),
    }


def _status_mismatches(actual_items, gold_items: list[dict]) -> list[dict]:
    """Per-item status mismatches between actual and gold."""
    gold_by_num = {it["item_number"].upper(): it["status"] for it in gold_items}
    actual_by_num = {it.item_number.upper(): it.status for it in actual_items}
    out = []
    for num in sorted(set(gold_by_num) & set(actual_by_num)):
        if gold_by_num[num] != actual_by_num[num]:
            out.append({"item": num, "expected": gold_by_num[num], "actual": actual_by_num[num]})
    return out


def run_eval(gold_dir: Path, *, with_xbrl: bool = True, with_llm: bool = False) -> dict:
    """Run the eval over every gold filing under gold_dir.

    Returns a dict {results: [...], summary: {...}} suitable for
    JSON serialization.
    """
    results = []
    for gold_path in sorted(gold_dir.glob("*.json")):
        gold = json.loads(gold_path.read_text())
        f = gold["filing"]
        t0 = time.perf_counter()
        try:
            actual = extract_10k(
                f["cik"], f["accession"],
                xbrl_validate=with_xbrl,
                enable_llm_aug=with_llm,
            )
            elapsed = int((time.perf_counter() - t0) * 1000)
            scores = score_filing(actual, gold)
            scores["wall_time_ms"] = elapsed
            scores["error"] = None
        except Exception as e:  # noqa: BLE001
            scores = {
                "items_actual": 0, "items_expected": len(gold.get("expected_items", [])),
                "item_recall": 0.0, "item_precision": 0.0, "status_accuracy": 0.0,
                "full_match_rate": 0.0, "text_alignment_rate": 0.0, "html_alignment_rate": 0.0,
                "extraction_time_ms": 0, "xbrl": {"validated": False},
                "wall_time_ms": int((time.perf_counter() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}",
                "fp_items": [], "fn_items": [], "status_mismatches": [],
            }
        results.append({"filing": gold_path.stem, "accession": f["accession"], **scores})

    n = len(results) or 1
    summary = {
        "filings": len(results),
        "mean_item_recall": round(sum(r["item_recall"] for r in results) / n, 4),
        "mean_item_precision": round(sum(r["item_precision"] for r in results) / n, 4),
        "mean_status_accuracy": round(sum(r["status_accuracy"] for r in results) / n, 4),
        "mean_full_match_rate": round(sum(r["full_match_rate"] for r in results) / n, 4),
        "mean_text_alignment": round(sum(r["text_alignment_rate"] for r in results) / n, 4),
        "mean_extraction_ms": int(sum(r["extraction_time_ms"] for r in results) / n),
        "errors": sum(1 for r in results if r.get("error")),
    }
    return {"results": results, "summary": summary}


def print_markdown_report(report: dict) -> None:
    print()
    print("# SEC 10-K Extractor Eval Report")
    print()
    print(f"**{report['summary']['filings']} gold filings**, "
          f"{report['summary']['errors']} errors")
    print()
    print("## Per-filing scores")
    print()
    print("| Filing | Items A/E | Recall | Prec | StatAcc | FullMatch | TextAlign | XBRL Facts | Time(ms) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in report["results"]:
        xbrl = r.get("xbrl") or {}
        xbrl_str = (
            f"{xbrl.get('total_facts', 0)}" if xbrl.get("has_xbrl_data") else "—"
        )
        print(
            f"| {r['filing']} | {r['items_actual']}/{r['items_expected']} "
            f"| {r['item_recall']:.2f} | {r['item_precision']:.2f} "
            f"| {r['status_accuracy']:.2f} | {r['full_match_rate']:.2f} "
            f"| {r['text_alignment_rate']:.2f} | {xbrl_str} | {r['wall_time_ms']} |"
        )
    print()
    print("## Aggregates")
    s = report["summary"]
    print(f"- mean recall          : {s['mean_item_recall']:.3f}")
    print(f"- mean precision       : {s['mean_item_precision']:.3f}")
    print(f"- mean status accuracy : {s['mean_status_accuracy']:.3f}")
    print(f"- mean full-match rate : {s['mean_full_match_rate']:.3f}")
    print(f"- mean text alignment  : {s['mean_text_alignment']:.3f}")
    print(f"- mean extraction time : {s['mean_extraction_ms']} ms")
    print()
    # Detail: status mismatches and missing items
    print("## Per-filing detail")
    for r in report["results"]:
        if r.get("error"):
            print(f"\n### {r['filing']}")
            print(f"  ERROR: {r['error']}")
            continue
        if r["fp_items"] or r["fn_items"] or r["status_mismatches"]:
            print(f"\n### {r['filing']}")
            if r["fn_items"]:
                print(f"  missing items (FN): {r['fn_items']}")
            if r["fp_items"]:
                print(f"  unexpected items (FP): {r['fp_items']}")
            if r["status_mismatches"]:
                print("  status mismatches:")
                for sm in r["status_mismatches"]:
                    print(f"    Item {sm['item']}: expected={sm['expected']!r} actual={sm['actual']!r}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gold-dir", type=Path,
                   default=Path(__file__).resolve().parent / "gold")
    p.add_argument("--no-xbrl", action="store_true",
                   help="Skip XBRL Company Facts fetch (faster, offline-friendly)")
    p.add_argument("--with-llm", action="store_true",
                   help="Enable Phase 2 LLM ensemble augmentation (requires "
                        "EXTRACTOR_AUG_MODELS in .env). Adds ~2-5 min per filing.")
    p.add_argument("--out", type=Path,
                   default=Path(__file__).resolve().parent / "last_run.json")
    args = p.parse_args()

    if not args.gold_dir.exists():
        print(f"gold dir not found: {args.gold_dir}", file=sys.stderr)
        return 2

    report = run_eval(
        args.gold_dir,
        with_xbrl=not args.no_xbrl,
        with_llm=args.with_llm,
    )
    args.out.write_text(json.dumps(report, indent=2, default=str))
    print_markdown_report(report)
    print(f"\n(JSON artifact saved to {args.out})")
    return 0 if report["summary"]["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
