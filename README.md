# sec-10k-extractor

Item-level structured extraction from SEC 10-K filings. Hybrid pipeline: deterministic rules first (free, fast), LLM augmentation only on edge cases, XBRL Company Facts cross-validation as a third sanity layer. Public Zeabur demo + library + CLI.

## Live demo

**https://sec-10k-extractor-kevin.zeabur.app**

The web UI offers:

- 10 quick-pick demo filings (3 gold + 7 silver) served from a pre-built cache. Zero cost, zero latency, zero SEC traffic.
- One-click sample chips (Costco / Starbucks / Nike / Domino's, all not in the cache) that fill the form so reviewers don't need to look up an accession on EDGAR.
- A free-form CIK + accession input that runs Phase 1 + XBRL live (rate-limited to 6 req/min/IP) with a staged progress indicator + elapsed timer; backend is sync so stages are heuristic, but they mirror the real `extract_10k()` phases.

Status badges colour-code the 6 outcomes so the cost of conflating them shows up visually.

---

## What this does

Given a 10-K filing identifier (CIK + accession number), produces a JSON file where each Item (Items 1, 1A, 1B, 1C, 2-4 of Part I; 5-9C of Part II; 10-14 of Part III; 15-16 of Part IV) is a record with:

```json
{
  "part": "I",
  "item_number": "1A",
  "item_title": "Risk Factors",
  "content_text": "...",
  "char_range_text": [12345, 67890],
  "char_range_html": [54321, 98765],
  "status": "extracted | incorporated_by_reference | not_applicable | reserved | partial | non_standard"
}
```

The `status` field is the load-bearing piece: real 10-Ks have wildly varying conventions (Part III commonly "incorporated by reference" pointing to the proxy; some Items marked Reserved or Not Applicable; SGML-era filings with completely different segmentation). Treating these uniformly as "missing data" is wrong; treating them per-status preserves downstream usability.

## Architecture

Three phases with cost discipline:

```
Phase 1: RULES (free, fast, deterministic)
  edgartools section parser + regex fallback for cross-reference TOCs.
  ↓ (low confidence on a small subset)
Phase 2: LLM augmentation (paid, K-vote ensemble)
  3 NIM-hosted models vote on status. Off by default; turn on per-run.
  Public Zeabur demo runs Phase 1 only.
  ↓
Phase 3: XBRL CROSS-VALIDATION (free)
  Cross-reference XBRL Company Facts API to verify cover-page metadata,
  fiscal-year alignment, and Item 8 numeric reconciliation.
```

Phase 1 hits 1.000 status accuracy on the 3-filing hand-validated gold set. 95%+ of items in modern filings resolve in Phase 1 alone.

## HTTP API

The same endpoints power the UI; you can call them directly with curl.

| Method + path | Purpose |
|---|---|
| `GET /health` | Liveness, demo-filing count, SEC_USER_AGENT presence flag |
| `GET /demo/filings` | 10-entry list with metadata (slug, label, cik, accession, characteristic) |
| `GET /demo/result/{slug}` | Full ExtractionResult JSON for a cached filing |
| `POST /extract` | Live Phase-1 extraction (rate-limited; 60s timeout) |

```bash
# Cached path: instant, no SEC traffic
curl https://sec-10k-extractor-kevin.zeabur.app/demo/result/apple-2024 | jq '.items | length'

# Live path: 3-30 sec, hits SEC EDGAR with the server's User-Agent
curl -X POST https://sec-10k-extractor-kevin.zeabur.app/extract \
  -H 'Content-Type: application/json' \
  -d '{"cik":"320193","accession":"0000320193-24-000123"}' | jq '.items[0]'
```

### Public demo policy

The public Zeabur instance runs **Phase 1 only** (deterministic rules + XBRL cross-check). Phase 2 LLM augmentation is intentionally not exposed via HTTP, so a public URL can't drain the operator's NIM quota. Phase 1 already reaches 1.000 status accuracy on the modern-filing gold set; the LLM augmentation is a Phase 2-specific improvement for older or amendment-only filings, and is opt-in for offline runs.

Other guardrails:

- **Rate limit**: 6 requests/min/IP via in-process token bucket; 4096-bucket LRU cap on the limiter map
- **Timeout**: 60s hard cap on /extract; 504 with a hint to use the demo cache for slow filings
- **CIK / accession format validation**: regex on both fields; 400 with helpful message on mismatch
- **Sanitised errors**: extraction failures surface the exception class + 240-char snippet, no stack traces

See `src/api/main.py` for the implementation and `tests/test_api.py` for the regression suite (12 tests).

## Evaluation

Hand-validated gold set + 7-filing silver set:

| Filing | Era | Notable |
|---|---|---|
| Apple 2024 | Modern HTML | 100% status accuracy on hand-validated gold spec |
| GE 2021 | Modern HTML | Phase 1 falls back to regex segmenter (cross-reference TOC) |
| Chemical Banking 1995 | SGML 10-K405 | Pre-iXBRL pure-text era; tests the SGML support path |

Plus 7 silver filings (Berkshire 2026/2019, Intel 2022/2020, Apple 2023, Goldman 2024 10-K/A, John Deere ABS) covering era boundaries, amendment-only filings, and Reg AB ABS schemas.

Results:

- **Gold (Phase 1 only)**: 1.000 status accuracy, 1.000 item recall, 1.000 item precision
- **Silver**: 0 invariant violations across all 7 filings
- **Phase 2 LLM ensemble**: ships off by default; see `docs/per-task/task3-llm-threshold-decision.md` for the K=2 vs K=3 honest-disclosure analysis (Chemical Banking 1995 gold-spec ambiguity, DeepSeek availability, threshold knob)

See `evals/sec-extraction/last_run.json` and per-filing JSONs in `evals/sec-extraction/gold/`.

## Quick start

### Library / CLI

```bash
pip install -e ".[dev]"
export SEC_USER_AGENT="Your Name you@example.com"

# Run on a single filing (CIK + accession)
python scripts/run_one_extract.py 320193 0000320193-24-000123

# Run the full silver-set eval
python -m evals.sec-extraction.silver_runner
```

### Web server (local)

```bash
export SEC_USER_AGENT="Your Name you@example.com"
python scripts/build_demo_cache.py    # one-time; populates ui/demo_cache/
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --app-dir src
# UI: http://localhost:8000
```

### Docker

```bash
docker build -t sec-10k-extractor .
docker run -p 8000:8000 -e SEC_USER_AGENT="Your Name you@example.com" sec-10k-extractor
```

## Cost / latency notes

For one modern 10-K:

- Phase 1 only: ~2-30 sec depending on filing size, $0
- + Phase 2 augmentation (offline): +5 sec, ~$0.005 (3-vote NIM ensemble, gated by confidence)
- + Phase 3 XBRL cross-check: +1 sec, $0

Modern multi-document 10-Ks (Berkshire-class) measured up to 184 sec end-to-end; for those, the demo cache is the right path.

## Repo layout

```
src/
├── api/                          FastAPI app + cache loader + rate limiter
│   ├── main.py                   /health /demo/filings /demo/result /extract + UI mount
│   ├── cache.py                  load demo manifest, serve pre-built JSONs
│   └── rate_limit.py             per-IP token bucket with LRU eviction
├── workers/extractor/            extraction pipeline
│   ├── pipeline.py               orchestrator (Phase 1 → Phase 2 → Phase 3)
│   ├── segment.py                edgartools-driven segmenter
│   ├── regex_segment.py          fallback for cross-reference TOCs
│   ├── classifier.py             status classifier (rules)
│   ├── align.py / iou.py         char-range alignment
│   ├── era.py                    items applicable per filing year
│   ├── cover_page.py             "incorporated by reference" detection
│   ├── xbrl_check.py             XBRL Company Facts cross-validator
│   ├── llm_assist.py             Phase 2 K-vote ensemble (off in public demo)
│   └── schema.py                 Pydantic models
├── shared/                       NIM client + retry decorator
ui/                               vanilla JS demo UI (no framework)
├── index.html / styles.css / app.js
└── demo_cache/                   per-filing pre-built JSONs + manifest
scripts/
├── run_one_extract.py            CLI smoke test
└── build_demo_cache.py           pre-build the demo cache before deploy
evals/sec-extraction/
├── gold/                         3 hand-validated filings (Apple 24, GE 21, Chem 95)
├── silver/                       7 filings + invariant-only validation
└── runner.py / silver_runner.py  eval harnesses
docs/per-task/                    task3-llm-threshold-decision.md and friends
prompts/                          conversation transcripts with Claude during build
tests/                            pytest suite (extractor + API)
```

## License

MIT, see `LICENSE`.
