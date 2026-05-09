"""SEC 10-K Item Extractor: FastAPI app for the public Zeabur demo.

Endpoints:

  GET  /health              liveness check
  GET  /demo/filings        list of 10 pre-cached demo filings
  GET  /demo/result/{slug}  full ExtractionResult JSON for a cached filing
  POST /extract             live Phase-1 extraction (rate-limited)

Public demo policy: Phase 1 (deterministic, free) only. Phase 2 LLM ensemble
augmentation is exposed in the library but not via this server, so a public
URL can't drain the operator's NIM quota. Phase 3 XBRL cross-validation runs
because it's free.

Static UI mounts at "/" last so the JSON endpoints win path matching first.
"""

from __future__ import annotations

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.cache import get_result, known_slug, list_filings
from api.rate_limit import RateLimiter

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIR = REPO_ROOT / "ui"

# SEC requires a User-Agent or returns 429. We refuse to start without one
# rather than silently using a generic-bot signature.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()

EXTRACT_RPM = int(os.environ.get("EXTRACT_RPM", "6"))

# Hard ceiling on a single live extraction. SEC fetch + XBRL + parsing for a
# Berkshire-class filing took ~3 minutes in our silver eval; cap so a hung
# request never holds a worker hostage. The timer also covers queue time, so
# under concurrent load slow requests can 504 before they get a worker;
# documented in the 504 message body.
EXTRACT_TIMEOUT_S = int(os.environ.get("EXTRACT_TIMEOUT_S", "60"))

# 4 workers covers a handful of concurrent evaluators while staying under
# SEC's 10 req/s ceiling (each extraction opens ~2-3 connections).
EXTRACT_WORKERS = int(os.environ.get("EXTRACT_WORKERS", "4"))

CIK_RX = re.compile(r"^\d{1,10}$")
ACCESSION_RX = re.compile(r"^\d{10}-\d{2}-\d{6}$")

_limiter = RateLimiter(capacity=EXTRACT_RPM, refill_per_minute=EXTRACT_RPM)
_extract_pool = ThreadPoolExecutor(max_workers=EXTRACT_WORKERS, thread_name_prefix="extract")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not SEC_USER_AGENT and os.environ.get("ALLOW_MISSING_SEC_UA") != "1":
        raise RuntimeError(
            "SEC_USER_AGENT environment variable is required. "
            "SEC EDGAR mandates a User-Agent identifying the requester. "
            "Set e.g. SEC_USER_AGENT='Your Name your@email'."
        )
    # Late import: tests that don't touch extraction can still import this module.
    if SEC_USER_AGENT:
        try:
            from edgar import set_identity

            set_identity(SEC_USER_AGENT)
        except Exception:  # noqa: BLE001
            pass
    # Warm the manifest so /health probes never touch disk.
    list_filings()
    yield
    # No pool shutdown: shutting down across test lifespans was breaking the
    # next TestClient's requests, and OS process exit reaps the threads anyway.


app = FastAPI(
    title="SEC 10-K Item Extractor",
    description=(
        "Item-level structured extraction from SEC 10-K filings. "
        "Phase 1 deterministic rules + Phase 3 XBRL cross-validation. "
        "Public demo cap: 6 requests/min/IP."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    cik: str = Field(..., min_length=1, max_length=10, description="SEC CIK (digits only)")
    accession: str = Field(..., description="Accession in 18-digit dashed form, e.g. 0000320193-24-000123")


class HealthResponse(BaseModel):
    status: str
    demo_filings: int
    sec_user_agent_set: bool


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        demo_filings=len(list_filings()),
        sec_user_agent_set=bool(SEC_USER_AGENT),
    )


@app.get("/demo/filings")
def demo_filings() -> dict:
    """List of pre-cached demo filings. UI uses this to render quick-pick buttons."""
    filings = list_filings()
    return {
        "count": len(filings),
        "filings": filings,
    }


@app.get("/demo/result/{slug}")
def demo_result(slug: str) -> dict:
    """Return the cached extraction result for a demo filing.

    503 (not 404) when the demo cache hasn't been built yet, to distinguish
    "this slug doesn't exist" (404) from "we know about it but cache isn't
    populated" (503).
    """
    if not known_slug(slug):
        raise HTTPException(status_code=404, detail=f"unknown demo slug: {slug}")
    data = get_result(slug)
    if data is None:
        raise HTTPException(
            status_code=503,
            detail=(
                f"demo cache for '{slug}' not yet built. "
                "Run scripts/build_demo_cache.py"
            ),
        )
    return data


@app.post("/extract")
def extract(req: ExtractRequest, request: Request) -> dict:
    """Run live Phase-1 (+ XBRL Phase-3) extraction for an arbitrary filing.

    Rate-limited per source IP. Times out at EXTRACT_TIMEOUT_S; for slow
    filings (modern Berkshires take minutes), point evaluators at the demo
    cache instead.
    """
    if not CIK_RX.match(req.cik):
        raise HTTPException(status_code=400, detail="cik must be 1-10 digits, no leading zeros required")
    if not ACCESSION_RX.match(req.accession):
        raise HTTPException(
            status_code=400,
            detail="accession must match NNNNNNNNNN-NN-NNNNNN (18 digits with two dashes)",
        )

    client_ip = _client_ip(request)
    allowed, retry_after = _limiter.allow(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit: {EXTRACT_RPM} req/min/IP. retry after {retry_after:.1f}s",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    if not SEC_USER_AGENT:
        raise HTTPException(
            status_code=503,
            detail="server misconfigured: SEC_USER_AGENT not set",
        )

    t0 = time.perf_counter()
    fut = _extract_pool.submit(_run_extract, req.cik, req.accession)
    try:
        result = fut.result(timeout=EXTRACT_TIMEOUT_S)
    except (TimeoutError, FuturesTimeout):
        raise HTTPException(
            status_code=504,
            detail=(
                f"extraction exceeded {EXTRACT_TIMEOUT_S}s (includes queue time). "
                "For slow filings (modern multi-document 10-Ks), use the demo cache."
            ),
        ) from None
    except _ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    result.setdefault("meta", {})["server_elapsed_ms"] = elapsed_ms
    return result


class _ExtractionError(Exception):
    pass


def _run_extract(cik: str, accession: str) -> dict:
    try:
        from workers.extractor.pipeline import extract_10k

        result = extract_10k(cik, accession, enable_llm_aug=False)
        return result.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001
        raise _ExtractionError(
            f"extraction failed: {type(exc).__name__}: {_short(exc)}"
        ) from None


def _short(exc: BaseException, limit: int = 240) -> str:
    s = str(exc).replace("\n", " ").strip()
    return s if len(s) <= limit else s[:limit] + "..."


def _client_ip(request: Request) -> str:
    # Trust the leftmost X-Forwarded-For entry. Rightmost is also defensible
    # under different threat models (it's the first hop the proxy added) but
    # leftmost matches Zeabur's documented behaviour.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
