"""Tests for the FastAPI demo server.

These tests do NOT hit SEC EDGAR. /extract is exercised with a monkey-patched
extract_10k so we can assert routing, validation, rate limiting and timeout
behaviour without network or NIM credentials.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ALLOW_MISSING_SEC_UA", "1")
os.environ.setdefault("EXTRACT_RPM", "3")  # tighter for tests

from api import main as api_main  # noqa: E402


@pytest.fixture
def client():
    api_main._limiter.reset()
    with TestClient(api_main.app) as c:
        yield c


def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["demo_filings"] >= 3  # at least the 3 gold; silver may or may not be present


def test_demo_filings_lists_known_keys(client):
    r = client.get("/demo/filings")
    assert r.status_code == 200
    body = r.json()
    slugs = {f["slug"] for f in body["filings"]}
    # Gold should always be available; silver depends on silver_filings.json existing.
    assert "apple-2024" in slugs
    assert "ge-2021" in slugs
    assert "chemical-banking-1995" in slugs
    assert body["count"] == len(body["filings"])


def test_demo_result_unknown_slug_404(client):
    r = client.get("/demo/result/this-slug-does-not-exist")
    assert r.status_code == 404


def test_demo_result_known_but_uncached_503(client):
    # apple-2024 is a known slug; ui/demo_cache/apple-2024.json hasn't been
    # built yet at this point in the development cycle.
    r = client.get("/demo/result/apple-2024")
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert "not yet built" in r.json()["detail"]


def test_extract_validates_cik(client):
    r = client.post("/extract", json={"cik": "abc", "accession": "0000320193-24-000123"})
    assert r.status_code == 400
    assert "cik" in r.json()["detail"].lower()


def test_extract_validates_accession(client):
    r = client.post("/extract", json={"cik": "320193", "accession": "bad-format"})
    assert r.status_code == 400
    assert "accession" in r.json()["detail"].lower()


def test_extract_invokes_pipeline_and_adds_server_elapsed(client, monkeypatch):
    captured: dict = {}

    def fake_extract(cik, accession):
        captured["cik"] = cik
        captured["accession"] = accession
        return {
            "filing": {
                "cik": cik, "accession": accession, "form_type": "10-K",
                "filing_date": "2024-11-01", "period_ending": "2024-09-28",
                "primary_document": "x.htm", "is_inline_xbrl": True, "is_abs_filing": False,
                "cover_page_incorporates": None,
            },
            "items": [],
            "meta": {"parser_version": "0.1.0", "extraction_time_ms": 42, "cost_usd": 0.0, "warnings": []},
        }

    monkeypatch.setattr(api_main, "_run_extract", fake_extract)
    monkeypatch.setattr(api_main, "SEC_USER_AGENT", "Test Suite test@example.com")

    r = client.post("/extract", json={"cik": "320193", "accession": "0000320193-24-000123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert captured == {"cik": "320193", "accession": "0000320193-24-000123"}
    assert body["meta"]["server_elapsed_ms"] >= 0


def test_extract_rate_limit(client, monkeypatch):
    # EXTRACT_RPM=3 in env means capacity 3, so the 4th hit in a burst is denied.
    monkeypatch.setattr(api_main, "_run_extract", lambda *a, **k: {"meta": {}})
    monkeypatch.setattr(api_main, "SEC_USER_AGENT", "Test Suite test@example.com")

    payload = {"cik": "320193", "accession": "0000320193-24-000123"}
    statuses = [client.post("/extract", json=payload).status_code for _ in range(4)]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429


def test_extract_502_on_pipeline_failure(client, monkeypatch):
    def boom(cik, accession):
        raise api_main._ExtractionError("extraction failed: ValueError: bad cik")

    monkeypatch.setattr(api_main, "_run_extract", boom)
    monkeypatch.setattr(api_main, "SEC_USER_AGENT", "Test Suite test@example.com")

    r = client.post("/extract", json={"cik": "320193", "accession": "0000320193-24-000123"})
    assert r.status_code == 502
    assert "extraction failed" in r.json()["detail"]


def test_extract_504_on_timeout(client, monkeypatch):
    def slow_extract(cik, accession):
        time.sleep(2.0)
        return {"meta": {}}

    monkeypatch.setattr(api_main, "_run_extract", slow_extract)
    monkeypatch.setattr(api_main, "SEC_USER_AGENT", "Test Suite test@example.com")
    monkeypatch.setattr(api_main, "EXTRACT_TIMEOUT_S", 1)

    r = client.post("/extract", json={"cik": "320193", "accession": "0000320193-24-000123"})
    assert r.status_code == 504


def test_extract_503_when_user_agent_missing(client, monkeypatch):
    monkeypatch.setattr(api_main, "SEC_USER_AGENT", "")
    monkeypatch.setattr(api_main, "_run_extract", lambda *a, **k: {"meta": {}})
    r = client.post("/extract", json={"cik": "320193", "accession": "0000320193-24-000123"})
    assert r.status_code == 503
    assert "SEC_USER_AGENT" in r.json()["detail"]


def test_rate_limiter_evicts_lru_when_full():
    """RateLimiter must not grow unbounded under unique-IP attack."""
    from api.rate_limit import MAX_BUCKETS, RateLimiter

    rl = RateLimiter(capacity=10, refill_per_minute=10)
    for i in range(MAX_BUCKETS + 50):
        rl.allow(f"ip-{i}")
    # After eviction, dict should be capped
    assert len(rl._buckets) < MAX_BUCKETS


def test_demo_result_returns_cached_payload_when_cache_built(client, tmp_path, monkeypatch):
    """When ui/demo_cache/{slug}.json exists, /demo/result/{slug} returns it verbatim."""
    from api import cache

    # Point the cache dir at a tmp dir, write a fake apple-2024.json
    fake = {"filing": {"cik": "320193"}, "items": [], "meta": {"parser_version": "0.1.0"}}
    (tmp_path / "apple-2024.json").write_text(json.dumps(fake))
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)

    r = client.get("/demo/result/apple-2024")
    assert r.status_code == 200
    assert r.json()["filing"]["cik"] == "320193"
