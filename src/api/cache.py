"""Demo cache: pre-rendered extraction results for the 3 gold + 7 silver filings.

The 10 demo entries are listed in `ui/demo_cache/manifest.json`. Per-entry
extraction results live at `ui/demo_cache/{slug}.json` and are produced by
`scripts/build_demo_cache.py`. The API serves these directly without hitting
SEC EDGAR or running the extractor: zero cost, zero latency, zero rate limits.

If the cache hasn't been built yet, list_filings() still works (it falls back
to deriving filing metadata from gold/silver source files) but
get_result(slug) returns None.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "ui" / "demo_cache"
MANIFEST_PATH = CACHE_DIR / "manifest.json"

GOLD_DIR = REPO_ROOT / "evals" / "sec-extraction" / "gold"
SILVER_FILINGS_PATH = REPO_ROOT / "evals" / "sec-extraction" / "silver" / "silver_filings.json"

GOLD_SLUGS = ["apple-2024", "ge-2021", "chemical-banking-1995"]

# Map slug → human-readable label. Keep in sync with the gold + silver source
# files; extra slugs fall back to the slug itself.
_PRETTY_LABELS = {
    "apple-2024": "Apple Inc. — FY2024",
    "ge-2021": "General Electric — FY2021",
    "chemical-banking-1995": "Chemical Banking — FY1995",
    "berkshire-2026": "Berkshire Hathaway — FY2025",
    "intel-2022": "Intel — FY2021",
    "apple-2023": "Apple Inc. — FY2023",
    "goldman-2024-10ka": "Goldman Sachs — FY2023 (10-K/A)",
    "john-deere-owner-trust-2024": "John Deere Owner Trust (ABS)",
    "berkshire-2019": "Berkshire Hathaway — FY2018",
    "intel-2020": "Intel — FY2019",
}

_GOLD_NOTES = {
    "apple-2024": "Modern iXBRL filing. Phase 1 hits 100% status accuracy on hand-validated gold spec.",
    "ge-2021": "Modern HTML; cross-reference TOC forces fallback to regex segmenter.",
    "chemical-banking-1995": "Pre-iXBRL SGML 10-K405 — pure-text era; tests the SGML support path.",
}


def load_gold_metadata() -> list[dict]:
    out = []
    for slug in GOLD_SLUGS:
        path = GOLD_DIR / f"{slug}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        filing = data.get("filing", {})
        out.append({
            "slug": slug,
            "label": _PRETTY_LABELS.get(slug, slug),
            "cik": filing.get("cik", ""),
            "accession": filing.get("accession", ""),
            "form_type": filing.get("form_type", "10-K"),
            "period_ending": filing.get("period_ending", ""),
            "characteristic": _GOLD_NOTES.get(slug, data.get("_comment", "")),
            "source": "gold",
        })
    return out


def load_silver_metadata() -> list[dict]:
    if not SILVER_FILINGS_PATH.exists():
        return []
    data = json.loads(SILVER_FILINGS_PATH.read_text())
    return [
        {
            "slug": f["key"],
            "label": _PRETTY_LABELS.get(f["key"], f["key"]),
            "cik": f.get("cik", ""),
            "accession": f.get("accession", ""),
            "form_type": f.get("form_type", "10-K"),
            "period_ending": f.get("period", ""),
            "characteristic": f.get("characteristic", ""),
            "source": "silver",
        }
        for f in data.get("filings", [])
    ]


def load_filings_metadata() -> list[dict]:
    """Single source of truth for the 10-filing list. Used by both the API
    (via list_filings) and scripts/build_demo_cache.py."""
    return load_gold_metadata() + load_silver_metadata()


@cache
def list_filings() -> list[dict]:
    """Return the 10 demo filings' metadata. Cached; restart to pick up edits."""
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text())["filings"]
    return load_filings_metadata()


@cache
def get_result(slug: str) -> dict | None:
    """Return the cached extraction result for slug, or None if not built."""
    try:
        return json.loads((CACHE_DIR / f"{slug}.json").read_text())
    except FileNotFoundError:
        return None


def known_slug(slug: str) -> bool:
    return any(f["slug"] == slug for f in list_filings())
