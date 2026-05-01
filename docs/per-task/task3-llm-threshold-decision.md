# Task 3 — LLM Augmentation Override Threshold (PRE-SUBMIT decision)

**Decision needed**: under what confidence does Phase-2 LLM ensemble vote
override Phase-1 deterministic status?

**Knob**: `LLM_AUG_OVERRIDE_THRESHOLD` env (default `0.51`).

## What we know from Day 4 eval

| Filing | Item | Phase 1 | LLM K=3 vote | Override under thresh=0.51 | Outcome |
|---|---|---|---|---|---|
| Chemical Banking 1995 | 9 | extracted | IBR (3/3, conf=1.0) | YES | **win** — gold-aligned |
| Chemical Banking 1995 | 10 | extracted | IBR (3/3, conf=1.0) | YES | **win** — gold-aligned |
| Chemical Banking 1995 | 11 | extracted | IBR (3/3, conf=1.0) | YES | **win** — gold-aligned |
| Chemical Banking 1995 | 12 | extracted | IBR (3/3, conf=1.0) | YES | **win** — gold-aligned |
| Chemical Banking 1995 | 7 | extracted | IBR (2/3, conf=0.67) | YES | **uncertain** — 2/3 split visible in audit |
| Apple 2024 | 1C | extracted | partial (?) | YES | **loss** — gold prefers extracted |
| Apple 2024 | 15 | extracted | partial (?) | YES | **loss** — gold prefers extracted |

Aggregate: gold status accuracy 1.000 → 0.923 with `--with-llm` at thresh=0.51.

## Three options

### Option A — keep `0.51` (current default)

- Captures all 4 Chemical Banking IBR wins.
- Eats 2 Apple losses (1C/15 reclassified to "partial").
- Net: −7.7 pp on a 13-status corpus = +4 wins, −2 losses, +2 ties (Item 7 split).
- **Defensible posture**: "LLM ensemble is leaned-in for IBR detection; gold-spec divergences are visible per-item in the eval JSON".

### Option B — raise to `0.99` (only unanimous)

- Keeps Chemical Banking 9-12 wins (already conf=1.0).
- Likely keeps Apple 1C/15 as Phase-1 "extracted" *if* their vote was 2/3, not 3/3.
- Loses Chemical Banking Item 7 (2/3 split — no override now, falls back to Phase 1 "extracted").
- Net effect under K=3: probably ≤ 1 status flip vs Option A; likely converges back near 1.000 status accuracy.
- **Cost**: throws away the framework's noise-tolerant property — without 2/3 majorities counting, the K=3 ensemble's variance reduction is wasted.

### Option C — per-status threshold

- IBR detection: keep `0.51` (LLM is good at within-document by-reference).
- "extracted" → "partial" reclassification: raise to `0.99` (Phase 1 is more conservative on partial, and LLM partial can be noisy).
- Other transitions: case-by-case.
- **Cost**: complexity. Three thresholds, three justifications, harder to defend in interview.

## K=3 cross-family (Qwen replaces DeepSeek) — VERIFIED 2026-05-01 night

After DeepSeek's NIM endpoint went unavailable, we wired in Qwen 3.5 122b
+ Gemma 4 31b as backup providers (both via NIM, openai-compatible). Ran
all role configs at K=3 with the new triple `nemotron + mistral + qwen`
(three uncorrelated training distributions).

**Result: status accuracy stays at 0.78** under threshold 0.51, 0.82
under threshold 0.99. Qwen agrees with Nemotron+Mistral on the same
"wrong" Chemical Banking 1995 overrides. The LLMs are NOT the noise
source.

| Run | Threshold | Apple | Chemical | GE | Mean |
|---|---|---|---|---|---|
| Phase 1 only | n/a | 1.00 | 1.00 | 1.00 | **1.000** |
| K=3 (deepseek+nemotron+mistral, Day 4) | 0.51 | 0.91 | 0.86 | 1.00 | 0.923 |
| K=2 (nemotron+mistral) | 0.51 | 0.91 | 0.43 | 1.00 | 0.780 |
| K=3 (nemotron+mistral+qwen) | 0.51 | 0.91 | 0.43 | 1.00 | 0.780 |
| **K=3 (nemotron+mistral+qwen)** | **0.99** | **0.96** | **0.50** | **1.00** | **0.819** |
| Apple + GE only, K=3 thresh=0.99 | 0.99 | 0.96 | — | 1.00 | **0.98** |

**The pathology is filing-specific, not provider-specific**: Chemical
Banking 1995 is a pre-iXBRL form 10-K405 (1995). Multiple LLM families
independently agree that 7-8 of its 14 items are "incorporated_by_reference"
where the gold annotator marked them "extracted". This is annotation
ambiguity in pre-iXBRL filings, not an LLM hallucination — the LLMs may
in fact be reading the document more accurately than the human did.

**On Apple 2024 + GE 2021 — modern iXBRL filings — the LLM augmentation
delivers 0.98 status accuracy** (only one Apple Item 15 disagreement,
which is a "partial" vs "extracted" judgment call where modern Item 15 is
itself a thin pointer to exhibit indices).

| Run | Status Acc | Apple mismatches | Chemical mismatches | GE mismatches |
|---|---|---|---|---|
| Phase 1 only (no LLM) | **1.000** | 0 | 0 | 0 |
| Phase 1 + LLM K=3 (DeepSeek+Nemotron+Mistral, thresh=0.51) | **0.923** | 2 (1C, 15) | 2 (6, 7) | 0 |
| Phase 1 + LLM K=2 (Nemotron+Mistral, thresh=0.51) | **0.780** | 2 (1C, 15) | 8 (6, 7, 9, 10, 11, 12, 13, 14) | 0 |

**Why K=2 is worse than K=3**: with three uncorrelated voters, DeepSeek
acted as a *variance check* — if Nemotron and Mistral both said IBR but
DeepSeek said extracted, the K=3 vote tied and `fallback_used=True`
returned the Phase 1 answer. Removing DeepSeek removes that brake; now
Nemotron+Mistral agreement (both Llama- / Mistral-derived families
trained on overlapping corpora) is more easily achieved on Chemical
Banking 1995 items where the LLM is over-confidently hallucinating
"incorporated_by_reference".

**This invalidates the original K=2-with-thresh-0.51 plan.** Threshold
0.99 doesn't help either — both models agree at confidence 1.0 on the
wrong answer.

## Revised recommendation — ship with LLM augmentation OFF for status

Three viable postures:

### A. Default OFF (recommended) — `enable_llm_aug=False` is the default

Pipeline already exposes `enable_llm_aug` parameter. Eval runner already
defaults `--with-llm` to OFF. **Change**: leave defaults as-is; document
that LLM augmentation is K=3-only and degraded under K=2. Status accuracy
on gold is then the deterministic 1.000.

### B. Effectively disable via threshold knob

Set `LLM_AUG_OVERRIDE_THRESHOLD=1.01` in `.env` (never satisfies
`vote.confidence < 1.01`, since confidence ≤ 1.0). LLM votes still get
collected and logged for audit; they just don't override Phase 1.

### C. Wait for DeepSeek availability + ship at K=3

Out of scope today.

**Ship with A.** README already states `--with-llm` is opt-in. The
`LLM_AUG_OVERRIDE_THRESHOLD=0.51` default stays as documented behavior
for K=3 — when DeepSeek returns, the knob means what it says.

## Action items

- [x] Make `_OVERRIDE_THRESHOLD` env-configurable via `LLM_AUG_OVERRIDE_THRESHOLD`.
- [x] Verify K=2 baseline against K=3 — confirms K=3 was variance-reducing,
      K=2 isn't.
- [x] Fix `_provider_lock` event-loop binding bug (was masking
      filings 2-3 LLM calls in earlier runs, hiding the K=2 regression).
- [ ] In README, label `--with-llm` as "K=3 ensemble required for the
      published 0.923 status accuracy. Under K=2 (current — DeepSeek
      offline) drops to 0.78; recommend running without `--with-llm` for
      production."
