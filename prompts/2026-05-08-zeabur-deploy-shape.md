# 2026-05-08: Choosing the Zeabur deploy shape

API vs API+UI vs cached demo.

The brief is unambiguous about Task 3: "在 Zeabur 部署為 API". But three honest readings exist for what counts as a deployable demo.

## What I asked

> 我想要先釐清現在 task 3 的狀態，包含
> 1. task 3 原題目敘述為何
> 2. 主要的目標為何
> 3. 我們做了哪些事
> 4. 有適合放在 Zeabur 部署或是 demo 的設計嗎

## The three options surfaced

### Option A: API only
A FastAPI server with `POST /extract`. Evaluators can `curl` it. Smallest scope (~3-4h). Honest, auditable.

Weakness: a JSON-only demo is the kind of submission that takes 30 seconds to evaluate; no visual hook for the "what does this actually produce" question.

### Option B: API + simple UI
Add a vanilla JS UI that calls `/extract` and renders the items table with status-coloured badges, char_range hover, content excerpt expand. ~6-8h.

Strength: a graders can see the *value* of the multi-status taxonomy at a glance: extracted vs IBR vs partial colour-codes the cost of conflating them. That's the differentiator over a plain-text "10-K parser".

### Option C: API + UI + pre-loaded demo filings
Option B plus a pre-rendered cache of the 3 gold + 7 silver filings, so demo buttons resolve instantly with zero SEC traffic. ~8-10h.

Strength: addresses the "Berkshire 2026 takes 184 sec to extract live, evaluator gets bored" failure mode. Also lets us hold a hard policy that the public server runs Phase 1 only: the cache covers the eval set, the live path covers anything else, and Phase 2 LLM augmentation never reaches the public URL.

## Decision

**Option C.** Three reasons:

1. **Cost discipline visible to the grader.** The grading axis explicitly calls out "成本紀律". Phase-1-only public demo + cached gold/silver filings is a *demonstration* of cost discipline, not just an asserted property. The build_demo_cache.py script makes the discipline auditable: SEC fetches happen at build time, never at request time.

2. **The 184-second silver filing.** Berkshire 2026's modern multi-document format genuinely takes 3 minutes end to end to extract. A live-only deploy would mean `curl` calls timing out for filings the grader is most likely to test (large modern conglomerates). The cached path covers that case without lying about how fast extraction actually is.

3. **The status taxonomy needs visual reinforcement.** The pitch of this Task is "treating extracted/IBR/N/A/reserved/partial uniformly is wrong". A status-coloured badge column makes that pitch in 2 seconds; a JSON dump makes it in 2 minutes. UI cost is paid back by evaluator-time saved.

## Trade-offs accepted

- **Public demo runs Phase 1 only.** This is enforced by hard-coding `enable_llm_aug=False` in `_run_extract`. The Phase 2 K-vote ensemble logic is in the codebase and runnable offline; documenting the threshold knob and Chemical Banking 1995 ambiguity is in `docs/per-task/task3-llm-threshold-decision.md`. We don't lose interview signal by hiding Phase 2 from the public URL; the threshold-decision doc is the place that shows the depth.

- **Cache files are checked into the repo (not built at container start).** Container start would mean every redeploy fires 10 SEC requests, hit rate-limits, and slow cold starts. Build-time + git-checked is reproducible and explicit.

- **Free-form input still goes live.** Per brief: "我們會用自己挑選的 filings 呼叫它". Hidden evaluators bring their own filings; cache wouldn't cover them. So `POST /extract` is the live path with rate-limit, 60s timeout, and sanitised errors.

## What this captures for the interviewer

The brief's "在 Zeabur 部署為 API" is a floor, not a ceiling. A bare `/extract` endpoint meets the letter; a cached demo + live fallback meets the spirit by showing cost discipline as a deployed property.

The call-out worth making in interview: cache-vs-live isn't the safe-vs-flashy axis. The cache is *more* honest because slow filings actually exist and `curl` timeouts hide that. The live fallback is the "I trust the system enough to run unseen inputs in 60s" claim.

## Phase plan (locked 2026-05-08)

A. FastAPI app + cache + rate-limit + 12 tests  
B. UI (10 buttons + free-form + items table with 6-status badges)  
C. Public-demo guardrails (rolled into A)  
D. `scripts/build_demo_cache.py` + run on the 10 filings  
E. Dockerfile + .dockerignore + .env.example  
F. README rewrite + this prompt doc  
G. Verify on Zeabur + screenshots  

Estimate: ~9 hours wall. Actual at time of writing: A through F finished in one session.
