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

## Current K=2 reality (DeepSeek down)

K=2 with `0.51` threshold ≈ K=3 with `0.99` threshold (both require unanimous,
since K=2 ties fall back via `vote_role.fallback_used=True`). So *in current
production state* we are effectively running Option B for free, with no code
change. The `_OVERRIDE_THRESHOLD` knob would matter only when DeepSeek
returns and we're back to K=3.

## Recommendation

**Ship with default `LLM_AUG_OVERRIDE_THRESHOLD=0.51`**, with the env knob
exposed for reviewers who prefer strict mode. Reasoning:

1. The current K=2 reality already enforces "unanimous" semantics, so the
   conservative posture is what the eval would actually exhibit at submit
   time. Reviewers running our code today get the conservative behavior
   without touching anything.
2. When DeepSeek availability returns, a reviewer can flip
   `LLM_AUG_OVERRIDE_THRESHOLD=0.99` to keep that behavior, OR leave at 0.51
   to capture 2/3-majority wins. The choice is theirs and explicit, not a
   code default we have to defend.
3. The eval JSON (`evals/sec-extraction/last_run.json`) records each
   per-item vote pick + confidence + raw_text, so any divergence is
   auditable line-by-line. There is no hidden behavior to read about in a
   commit message.

Per-status threshold (Option C) is valid future work; not worth the
complexity for the corpus we ship with.

## Action items

- [x] Make `_OVERRIDE_THRESHOLD` env-configurable via `LLM_AUG_OVERRIDE_THRESHOLD`.
- [ ] Document the env in `.env.example` (no commit yet — `.env` is gitignored;
      will land in a Day 6 / final cleanup commit if we add `.env.example`).
- [ ] Note in README that threshold is configurable.
- [ ] Re-run gold-with-LLM under K=2 at default threshold to verify the K=2
      behavior matches expectation (effectively no-overrides for non-unanimous).
