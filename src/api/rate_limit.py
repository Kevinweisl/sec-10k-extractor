"""Per-IP token bucket rate limiter.

In-process, single-instance. Suits a one-container Zeabur deployment fronting
a public demo. For multi-replica setups we'd swap this for a Redis-backed
counter; not in scope here.

Default policy for /extract: 6 requests / minute / IP. SEC's own rule is
10 req/sec, and the extractor opens up to ~5 connections for one filing
(filing fetch + XBRL + a couple of asset fetches), so 6/min keeps us well
under SEC's ceiling even at full saturation.

Memory bound: when the bucket map exceeds MAX_BUCKETS, we evict the least
recently active half. Without this, a long-running demo accumulating one
bucket per unique source IP could grow unbounded.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

MAX_BUCKETS = 4096


@dataclass
class _Bucket:
    tokens: float
    last_refill_ts: float


class RateLimiter:
    def __init__(self, *, capacity: int = 6, refill_per_minute: int = 6) -> None:
        self._capacity = float(capacity)
        self._refill_rate = refill_per_minute / 60.0  # tokens per second
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """Try to consume one token for the given key.

        Returns (allowed, retry_after_seconds). When allowed=True, retry_after
        is 0; when False, it's the seconds the caller should wait before
        retrying.
        """
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) >= MAX_BUCKETS:
                self._evict_lru_locked()

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._capacity, last_refill_ts=now)
                self._buckets[key] = bucket
            else:
                elapsed = now - bucket.last_refill_ts
                bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_rate)
                bucket.last_refill_ts = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            deficit = 1.0 - bucket.tokens
            wait = deficit / self._refill_rate if self._refill_rate > 0 else 60.0
            return False, wait

    def _evict_lru_locked(self) -> None:
        """Drop the half of buckets least recently active. Lock must be held."""
        keep = sorted(self._buckets.items(), key=lambda kv: kv[1].last_refill_ts)
        cutoff = len(keep) // 2
        self._buckets = dict(keep[cutoff:])

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
