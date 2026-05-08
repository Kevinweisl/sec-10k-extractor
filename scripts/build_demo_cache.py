"""Build the public-demo cache: per-filing ExtractionResult JSON + manifest.

For each of the 3 gold + 7 silver filings, run `extract_10k(cik, accession,
enable_llm_aug=False)` and dump:
  - `ui/demo_cache/{slug}.json` (full ExtractionResult, served by /demo/result/{slug})
  - `ui/demo_cache/manifest.json` (the metadata listing served by /demo/filings)

This script is the ONLY path that triggers SEC fetches at build time. The
running web server never touches SEC EDGAR for cache hits — that's the whole
point of pre-rendering.

Usage:
  export SEC_USER_AGENT="Your Name you@example.com"
  python scripts/build_demo_cache.py [--only slug1,slug2] [--force]

Flags:
  --only   comma-separated slug list to (re)build; default = all 10
  --force  rebuild even if {slug}.json already exists
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from api.cache import load_filings_metadata  # noqa: E402

CACHE_DIR = REPO_ROOT / "ui" / "demo_cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"


def _ensure_user_agent() -> None:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        sys.stderr.write(
            "ERROR: SEC_USER_AGENT environment variable required.\n"
            "  export SEC_USER_AGENT='Your Name you@example.com'\n"
        )
        sys.exit(2)
    try:
        from edgar import set_identity

        set_identity(ua)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"WARN: edgartools set_identity failed: {exc}\n")


def _build_one(slug: str, cik: str, accession: str) -> dict:
    from workers.extractor.pipeline import extract_10k

    t0 = time.perf_counter()
    result = extract_10k(cik, accession, enable_llm_aug=False)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    payload = result.model_dump(mode="json")
    # Stamp build provenance so the UI can show "cached on YYYY-MM-DD".
    payload.setdefault("meta", {})["cache_built_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["meta"]["cache_build_elapsed_ms"] = elapsed_ms
    return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated slugs (default: all)")
    ap.add_argument("--force", action="store_true", help="rebuild even if cache exists")
    args = ap.parse_args()

    _ensure_user_agent()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    all_filings = load_filings_metadata()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        filings = [f for f in all_filings if f["slug"] in wanted]
        if not filings:
            sys.stderr.write(f"ERROR: no matching slugs from {args.only!r}\n")
            return 2
    else:
        filings = all_filings

    print(f"Building demo cache for {len(filings)} filings -> {CACHE_DIR}")
    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    for f in filings:
        slug = f["slug"]
        out = CACHE_DIR / f"{slug}.json"
        if out.exists() and not args.force:
            print(f"  [skip] {slug}: {out.name} already exists (use --force to rebuild)")
            successes.append(slug)
            continue

        print(f"  [run]  {slug}: cik={f['cik']} accession={f['accession']} ...", flush=True)
        try:
            payload = _build_one(slug, f["cik"], f["accession"])
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
            n_items = len(payload.get("items", []))
            print(f"         {slug}: {n_items} items -> {out.relative_to(REPO_ROOT)}")
            successes.append(slug)
        except Exception as exc:  # noqa: BLE001
            print(f"         {slug}: FAILED {type(exc).__name__}: {exc}")
            failures.append((slug, str(exc)))

    # Manifest covers all 10 filings (not just the ones rebuilt this run) so
    # /demo/filings is deterministic regardless of partial rebuilds.
    MANIFEST_PATH.write_text(
        json.dumps({"filings": all_filings}, indent=2, ensure_ascii=False)
    )
    print(f"  manifest: {len(all_filings)} entries -> {MANIFEST_PATH.relative_to(REPO_ROOT)}")

    print()
    print(f"Success: {len(successes)}/{len(filings)} ({', '.join(successes) or 'none'})")
    if failures:
        print(f"Failures: {len(failures)}")
        for slug, msg in failures:
            print(f"  - {slug}: {msg[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
