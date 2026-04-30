"""Find a specific 10-K filing by (cik, accession) using edgartools.

edgartools handles SEC EDGAR rate limiting + caching internally (its cache
lives in ~/.edgar/). We add a thin lookup layer because the bare `Filing(...)`
constructor needs metadata (filing_date, company) that we'd otherwise have to
fetch ourselves.
"""

from __future__ import annotations

import os

from edgar import Company, Filing, set_identity

# SEC requires User-Agent identification — set once at import time.
_IDENTITY = os.environ.get(
    "SEC_USER_AGENT",
    "Kevin Wei interview-hw-2026 weisl@nlg.csie.ntu.edu.tw",
)
set_identity(_IDENTITY)
# edgartools also reads EDGAR_IDENTITY
os.environ.setdefault("EDGAR_IDENTITY", _IDENTITY)


def find_10k_filing(cik: int | str, accession: str) -> Filing:
    """Look up a 10-K filing by accession number under the given CIK.

    Raises ValueError if not found.
    """
    cik_int = int(cik)
    company = Company(cik_int)
    # cover both '10-K' and amendments
    filings = company.get_filings(form=["10-K", "10-K/A", "10-K405"])
    for f in filings:
        if f.accession_no == accession:
            return f
    raise ValueError(
        f"10-K with accession={accession!r} not found under CIK={cik_int} "
        f"(searched {len(filings)} filings)"
    )
