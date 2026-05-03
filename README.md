# sec-10k-extractor

Item-level structured extraction from SEC 10-K filings. Hybrid pipeline: deterministic rules first (free, fast), LLM augmentation only on edge cases, XBRL Company Facts cross-validation as a third sanity layer.

> Origin: AI Coding Test 2026 — interview deliverable. Task 3 of three. Designed to be the depth-anchor of the submission.

## What this does

Given a 10-K filing identifier (CIK + accession number, or filename URL), produces a JSON file where each Item (Items 1, 1A, 1B, 1C, 2-4 of Part I; 5-9C of Part II; 10-14 of Part III; 15-16 of Part IV) is a record with:

```json
{
  "part": "I",
  "item_number": "1A",
  "item_title": "Risk Factors",
  "content_text": "...",
  "char_range": [12345, 67890],
  "status": "extracted | incorporated_by_reference | not_applicable | reserved | partial | non_standard"
}
```

The `status` field is the load-bearing piece: real 10-Ks have wildly varying conventions (Part III commonly "incorporated by reference" pointing to the proxy; some Items marked Reserved or Not Applicable; SGML-era filings with completely different segmentation). Treating these uniformly as "missing data" is wrong; treating them per-status preserves downstream usability.

## Architecture

Three phases with cost discipline:

```
Phase 1 — RULES (free, fast, deterministic)
  edgartools section parser → if confident, done.
  ↓ (low confidence)
Phase 2 — LLM augmentation (paid, K-vote ensemble)
  3 NIM-hosted models vote on status when rules are unsure.
  Only fired below a confidence threshold.
  ↓
Phase 3 — XBRL CROSS-VALIDATION (free, sanity check)
  Cross-reference XBRL Company Facts API to verify cover-page
  metadata, fiscal-year alignment.
```

Cost discipline: 95%+ of items resolve in Phase 1 alone on modern filings; Phase 2 fires only on edge cases (Part III incorporated-by-reference, SGML-era filings, etc.).

## Evaluation

Hand-validated gold set + 7-filing silver set:

| Filing | Era | Notable |
|---|---|---|
| Apple 2024 | Modern HTML | 100% status accuracy |
| GE 2021 | Modern HTML | Phase 1 falls back to regex segmenter (cross-reference TOC) |
| Chemical Banking 1995 | SGML 10-K405 | Pure-text era; tests the SGML support path |

Results: silver-set extraction across 7 filings, 0 violations of declared invariants. See `evals/sec-extraction/silver/last_run.json` and per-filing JSONs in `evals/sec-extraction/gold/`.

## Quick start

```bash
pip install -e ".[dev]"

# Set env vars in .env
export NIM_BASE_URL_NEMOTRON="..."
export NIM_BASE_URL_MISTRAL="..."
export NIM_BASE_URL_LLAMA="..."

# Run on a single filing (CIK + accession)
python scripts/run_one_extract.py \
    --cik 0000320193 --accession 0000320193-24-000123 \
    --out /tmp/apple-2024.json

# Run the full silver-set eval
python -m evals.sec-extraction.silver_runner
```

## Cost / latency notes

For one modern 10-K:

- Phase 1 only: ~2 sec, $0
- + Phase 2 augmentation: +5 sec, ~$0.005 (3-vote NIM ensemble)
- + Phase 3 XBRL cross-check: +1 sec, $0

Total ~8 sec, ~$0.005 per filing on the modern path. SGML-era filings skip Phase 3 (no XBRL).

## License

MIT — see `LICENSE`.
