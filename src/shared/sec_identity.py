"""Single source of truth for SEC EDGAR User-Agent identity.

SEC EDGAR mandates a User-Agent header identifying the requester
(https://www.sec.gov/os/accessing-edgar-data). Without it requests get a 429.

Three call sites historically read SEC_USER_AGENT independently and disagreed
on the default; this module is the only place that decision lives.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_FALLBACK = "Kevin Wei interview-hw-2026 weisl@nlg.csie.ntu.edu.tw"


def get_user_agent(*, strict: bool = False) -> str:
    """Return the configured SEC User-Agent.

    strict=True: raise if SEC_USER_AGENT is unset (use from server entry points
    that should refuse to start without one).
    strict=False: fall back to the project default (use from worker modules
    that may be imported from notebooks / smoke tests).
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if ua:
        return ua
    if strict:
        raise RuntimeError(
            "SEC_USER_AGENT environment variable is required. "
            "SEC EDGAR mandates a User-Agent identifying the requester. "
            "Set e.g. SEC_USER_AGENT='Your Name your@email'."
        )
    return _FALLBACK


def set_edgar_identity() -> None:
    """Tell edgartools who we are. Safe to call multiple times."""
    ua = get_user_agent()
    try:
        from edgar import set_identity

        set_identity(ua)
    except ImportError:
        log.warning("edgartools not importable; skipping set_identity")
    except Exception as exc:  # noqa: BLE001
        log.warning("edgartools set_identity failed: %s", exc)
    os.environ.setdefault("EDGAR_IDENTITY", ua)
