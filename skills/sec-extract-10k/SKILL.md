---
name: sec-extract-10k
description: |
  Extract SEC Form 10-K item-level structured content (Items 1, 1A, 1B, 1C, 2-4
  in Part I; 5-9C in Part II; 10-14 in Part III; 15-16 in Part IV) from a
  filing identified by CIK + accession number. Use this when the user asks to
  "extract the 10-K", "parse Item 1A risk factors", "get the financial
  statements section", "pull the SEC annual report", "what's in Item 7 of
  Apple's 10-K", "compare the cybersecurity disclosure across years", or any
  request that names an SEC 10-K filing or specific 10-K Item. Do NOT use for:
  10-Q quarterly reports (out of scope), 8-K event filings, DEF 14A proxy
  statements, or general SEC filing search (use SEC EDGAR full-text). Returns
  per-item structured records with status (extracted | incorporated_by_reference
  | not_applicable | reserved | partial | non_standard), char ranges, era
  applicability, and (optional) XBRL Company Facts cross-validation.
allowed-tools: ""
worker_target: extractor
---

# sec-extract-10k

Hybrid rules + LLM extraction pipeline for SEC Form 10-K filings. Three phases:

## Pattern: Hybrid rules+LLM with cost discipline

| Phase | Cost | Coverage |
|---|---|---|
| **Phase 1** rules-based segmentation + status classification | free, deterministic | ~80% of items unambiguously classified |
| **Phase 2** K=3 LLM ensemble (DeepSeek + Nemotron + Mistral) on Phase-1 uncertain cases | ~$0.05/filing typical | the long tail of within-document by-references, partial disclosures |
| **Phase 3** XBRL Company Facts cross-validation | one HTTP call | sanity-checks Item 8 financial numbers against tagged XBRL data |

Phase 1 catches era-aware item applicability (Item 1C only since FY ending
2023-12-15; 9C only since 2022-01-10; Item 6 = [Reserved] since 2021;
1A/1B since 2005-12-01) so the output honestly reports which items the era
required.

## Edge cases handled

- **GE 2021 cross-reference TOC filings**: body is topical, TOC at end is the
  canonical Item declaration; special parser path.
- **Chemical Banking 1995 SGML era (form 10-K405)**: pre-iXBRL, edgartools
  rejects the form; falls through to regex-based segmentation.
- **ABS Reg AB filings (e.g., John Deere Owner Trust)**: detected via Reg AB
  Item 11XX patterns; returns single `non_standard` placeholder.
- **10-K/A amendments**: only Items present in the amendment are returned.

## Input
```json
{
  "cik": "320193",                    // Apple
  "accession": "0000320193-24-000123",
  "enable_llm_aug": false,            // Phase 2; off by default
  "xbrl_validate": true               // Phase 3
}
```

## Output
```json
{
  "filing": {"cik": "...", "accession": "...", "form_type": "10-K", "filing_date": "...", "period_ending": "..."},
  "items": [{"part": 1, "item_number": "1A", "status": "extracted",
             "content_text": "...", "char_range_text": [11751, 27518], ...}],
  "xbrl_validation": {"has_xbrl_data": true, "total_facts_for_accession": 429, ...},
  "meta": {"extraction_time_ms": 28000, "warnings": [...]}
}
```

## Eval-set evidence

Three hand-validated gold filings (Apple 2024, GE 2021, Chemical Banking 1995)
score 100% recall / precision / status accuracy / full-match. Seven silver
filings (Berkshire 2026/2019, Intel 2022/2020, Apple 2023, Goldman 2024 10-K/A,
John Deere ABS 2024) pass all structural-constraint checks. See
`evals/sec-extraction/`.
