"""Find a specific 10-K filing by (cik, accession) using edgartools.

edgartools handles SEC EDGAR rate limiting + caching internally (its cache
lives in ~/.edgar/). We add a thin lookup layer because the bare `Filing(...)`
constructor needs metadata (filing_date, company) that we'd otherwise have to
fetch ourselves.
"""

from __future__ import annotations

from edgar import Company, Filing

from shared.sec_identity import set_edgar_identity

set_edgar_identity()


def find_10k_filing(cik: int | str, accession: str) -> Filing:
    """Look up a 10-K filing by accession number under the given CIK.

    Raises ValueError if not found.
    """
    cik_int = int(cik)
    company = Company(cik_int)
    filings = company.get_filings(form=["10-K", "10-K/A", "10-K405"])
    for f in filings:
        if f.accession_no == accession:
            return f
    raise ValueError(
        f"10-K with accession={accession!r} not found under CIK={cik_int} "
        f"(searched {len(filings)} filings)"
    )
