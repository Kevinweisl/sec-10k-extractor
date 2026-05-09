# Task 3: Zeabur Deploy Plan (Option C: API + UI + cached demo filings)

Date: 2026-05-08
Goal: 在 Zeabur 部署 `sec-10k-extractor` 為公開可用的 API + 視覺化 demo，符合題目「在 Zeabur 部署為 API」要求。

## Scope decision (locked)

Option C: **API + 簡易 UI + 預載 demo filings**.
- 公開 demo 只開 Phase 1（免 NIM cost）
- 10 個預跑 filings（3 gold + 7 silver）的結果直接 serve 靜態 cache
- 自由輸入 (CIK + accession) 走 live Phase 1 only

Out of scope:
- Public Phase 2 LLM augmentation（避免別人燒 NIM quota；保留 env knob）
- 認證系統（無；公開 read-only API）
- Persistent DB（不需要；filing 結果無狀態）

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser (vanilla JS UI)                            │
│  - 10 quick-pick buttons (cached)                   │
│  - Free-form input (live Phase 1)                   │
└────────────────────┬────────────────────────────────┘
                     │ HTTP
┌────────────────────▼────────────────────────────────┐
│  FastAPI app (src/api/main.py)                      │
│  ├ GET  /              → static UI                  │
│  ├ GET  /health        → liveness                   │
│  ├ GET  /demo/filings  → list of 10 cached          │
│  ├ GET  /demo/result/{slug} → cached JSON           │
│  └ POST /extract       → live Phase 1, rate-limited │
└────────────────────┬────────────────────────────────┘
                     │ pipeline.extract_10k(cik, accession, enable_llm_aug=False)
┌────────────────────▼────────────────────────────────┐
│  Existing extractor (src/workers/extractor/)        │
│  Phase 1 deterministic only on public deploy        │
└─────────────────────────────────────────────────────┘
```

## Phases (順序執行)

### Phase A: HTTP API skeleton ✓ DONE 2026-05-08
- [x] `src/api/__init__.py`
- [x] `src/api/main.py`; FastAPI app, 4 endpoints + CORS + lifespan + sanitised errors
- [x] `src/api/cache.py`; list_filings derives 10 entries from gold + silver_filings.json source files (manifest at `ui/demo_cache/manifest.json` overrides if present)
- [x] `src/api/rate_limit.py`; per-IP token bucket, MAX_BUCKETS=4096 LRU eviction
- [x] `pyproject.toml`; `fastapi>=0.115` + `uvicorn[standard]>=0.32` added
- [x] `tests/test_api.py`; 12 tests, all pass

#### Phase A review (3 iterations)
- **Iter 1 issues fixed**: `concurrent.futures.TimeoutError` not aliased to builtin on Py3.10 → tuple catch; lifespan pool shutdown was killing across tests → removed shutdown
- **Iter 2 issues fixed**: ruff (import order, nested if) → auto-fix; over-engineered `_get_pool()` accessing private `_shutdown` attr → simplified to module-level pool
- **Iter 3 issues fixed**: RateLimiter unbounded dict growth under unique-IP attack → MAX_BUCKETS=4096 LRU eviction + test
- **Real-uvicorn smoke**: /health (10 demo filings, UA flag), /demo/filings (gold + silver merged), /demo/result/apple-2024 (503 with action hint), /extract bad cik (400 with sane message). All endpoints behave correctly.
- **Acceptable trade-offs**: orphan threads on /extract timeout (sync extractor can't be cancelled); pool=2 + 60s cap bounds the blast radius. `@cache` on list_filings means manifest changes need restart; fine since Phase D writes manifest before server start.

### Phase B: UI ✓ DONE 2026-05-08
- [x] `ui/index.html`; header + intro + demos (gold/silver) + live form + result section
- [x] `ui/styles.css`; colour palette mirrors Task 1; 6 status-badge classes (extracted=green, IBR=yellow, N/A=gray, reserved=gray, partial=orange, non_standard=blue); 640px responsive breakpoint
- [x] `ui/app.js`; bootstrap fetches /demo/filings, renders 3+7 buttons; demo path + live path; item row click expands content excerpt + char_range; sanitised HTML via escapeHtml everywhere
- [x] FastAPI StaticFiles mount already in Phase A main.py

#### Phase B verification (Chrome MCP)
- 10 demo buttons render with correct labels (Apple FY2024, GE FY2021, Chemical Banking FY1995, Berkshire FY2025, Intel FY2021, Apple FY2023, Goldman FY2023 10-K/A, John Deere ABS, Berkshire FY2018, Intel FY2019)
- Mock-fetch test: 8-item ExtractionResult renders → 4 part groups (Cover/synthetic, Part I, II, III) + 6 status-badge variants visible + filing meta header + IBR cover note + footer (XBRL 466 facts, parser, cost)
- Item row click: expands inline detail with content excerpt (max 1500 chars) + char_range
- 503 error path (cache not built): "demo cache for 'apple-2024' not yet built" rendered as red error
- 400 error path (bad CIK): "cik must be 1-10 digits..." rendered as red error
- No JS console errors

#### Acceptable trade-offs
- No animated spinner during 3-30 sec live extract (status text only)
- ABS filing items appear under "Cover / synthetic" part group (item.part=0); semantically ok since the synthetic abs record is filing-level not Item-level
- `roman(0)` dead code; safe since part=0 always routed to "Cover / synthetic" label

### Phase C: Public demo guardrails ✓ DONE 2026-05-08 (mostly subsumed by Phase A)
- [x] FastAPI 只暴露 Phase 1（`enable_llm_aug=False` 硬編碼在 `_run_extract`）. verified by source-inspection
- [x] `SEC_USER_AGENT` 必填，lifespan 啟動 fail-fast；測試模式以 `ALLOW_MISSING_SEC_UA=1` 跳過
- [x] Per-IP rate limit 6 req/min/IP, 4096 buckets LRU
- [x] `/extract` 60s timeout (504 + helpful message)
- [x] CORS `allow_origins=["*"]`, methods limited to GET/POST
- [x] CIK regex `^\d{1,10}$`, accession regex `^\d{10}-\d{2}-\d{6}$`
- [x] Error sanitisation: `_short()` truncates exception messages to 240 chars; `from None` suppresses traceback chain
- [x] `.env.example` documents required + optional vars (added in Phase C wrap-up)

### Phase D: Pre-cache 10 filings ✓ DONE 2026-05-08 (script written, cache build in progress)
- [x] gold + silver source JSONs are summary-only, NOT full ExtractionResult; must call `extract_10k` for each filing
- [x] `scripts/build_demo_cache.py`; runs `extract_10k(cik, accession, enable_llm_aug=False)` per filing, dumps to `ui/demo_cache/{slug}.json` + manifest. Stamps `cache_built_at` provenance.
- [x] cache files git-checked-in (NOT built at container start); keeps deploy reproducible + avoids 10 SEC fetches per cold-start
- [x] Single-filing smoke (apple-2024): 24 items, XBRL 429 facts, 51 sec
- [ ] Full 10-filing cache build running in background; berkshire-2026 + intel-2022/2020 expected to take 2-3 min each (memory says 184s for berkshire-2026 in silver eval)

### Phase E: Dockerfile + Zeabur ✓ DONE 2026-05-08 (deploy steps queued for user)
- [x] `Dockerfile`; python:3.12-slim, copy src + ui + evals (evals needed for cache fallback), pip install -e ., uvicorn CMD
- [x] `.dockerignore`; excludes .git, prompts, tests, scripts, docs, tasks, .env*
- [x] `.env.example`; SEC_USER_AGENT (required) + EXTRACT_RPM, EXTRACT_TIMEOUT_S, NIM_* (offline only)
- [ ] 本機 docker build 驗證 (running in background)
- [ ] 推 GitHub (user action: git add + push)
- [ ] Zeabur GitHub App 安裝 (user action) → auto-deploy
- [ ] Aliyun Bangkok 服務器 (per memory, already purchased)

### Phase F: README + docs ✓ DONE 2026-05-08
- [x] README rewritten; sections: What this does / Architecture / Live demo / HTTP API (with curl examples) / Public demo policy (Phase 1 only + guardrails) / Evaluation / Quick start (CLI + server + docker) / Cost & latency / Repo layout
- [x] API contract section: 4 endpoints with curl examples + sanitised-error policy
- [x] Public demo policy explained: enable_llm_aug=False hardcoded, rate-limit, timeout, validation
- [x] `prompts/2026-05-08-zeabur-deploy-shape.md`; 3-option decision (API only / API+UI / API+UI+cached) and why C wins on cost discipline + 184s Berkshire reality + status taxonomy visual reinforcement
- [x] Live demo URL placeholder; will fill in after Zeabur deploy

### Phase G: Verify
- [ ] 部署後 prod 端點 200 OK
- [ ] /demo/filings 回 10 entries
- [ ] 隨機點 3 個 demo button 結果正確
- [ ] Free-form 跑一個非預載 filing（例如 Microsoft 2024）成功
- [ ] 故意丟壞 CIK 看錯誤訊息漂亮
- [ ] 截圖 4-5 張存到 `docs/screenshots/`
- [ ] 更新 `~/src/interview_hw/SUBMISSION.md` 含 Task 3 prod URL

## 估時

| Phase | 估時 |
|---|---|
| A. API skeleton | 1.5h |
| B. UI | 2.5h |
| C. Guardrails | 1h |
| D. Pre-cache | 0.5h |
| E. Dockerfile + deploy | 1.5h |
| F. README | 1h |
| G. Verify | 1h |
| **Total** | **~9h** |

## Risks

| Risk | Mitigation |
|---|---|
| edgartools 在 Zeabur container 行為與本機不同（HTTP/SSL/UA） | Dockerfile 早期 `pip install` + 一次 smoke test 跑 Apple 2024；早失早改 |
| SEC rate limit 在公開環境被 demo 觸發 | rate_limit.py + cached demo 是首選 path |
| Container memory 吃太多（edgartools + lxml） | Aliyun Bangkok 4GB 足夠；先量再說 |
| Free-form 輸入觸發 LLM 路徑 | endpoint 寫死 `enable_llm_aug=False` |

## Review section（部署後填）

- [ ] 部署 URL：________
- [ ] 預載 demo button 全部正確：☐
- [ ] Free-form 真的跑得起來：☐
- [ ] 成本紀錄（Aliyun + 任何 NIM 偷跑）：________
- [ ] 螢幕截圖數：____
- [ ] Lessons 學到的點：________
