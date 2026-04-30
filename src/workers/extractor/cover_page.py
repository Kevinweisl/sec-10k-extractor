"""Detect the 'DOCUMENTS INCORPORATED BY REFERENCE' block on a 10-K cover page.

Apple 2024 example (verbatim):

    DOCUMENTS INCORPORATED BY REFERENCE
    Portions of the Registrant's definitive proxy statement relating to its
    2025 annual meeting of shareholders are incorporated by reference into
    Part III of this Annual Report on Form 10-K where indicated.

We extract the year (2025) so a downstream resolver can find the matching
DEF 14A filing within the 120-day window post-fiscal-year-end.
"""

from __future__ import annotations

import re

# Look for "DOCUMENTS INCORPORATED BY REFERENCE" header followed (within a window) by
# a 4-digit year + "annual meeting" or "proxy statement"
_RE_BLOCK = re.compile(
    r"DOCUMENTS\s+INCORPORATED\s+BY\s+REFERENCE\b.{0,2000}?"
    r"(\d{4})\s+(?:annual\s+meeting|proxy\s+statement|definitive\s+proxy)",
    re.IGNORECASE | re.DOTALL,
)


def detect_cover_incorporates(text: str) -> dict | None:
    """Return {target_form, expected_year, ...} or None if not found.

    Result schema matches schema.ReferencedFiling.
    """
    m = _RE_BLOCK.search(text)
    if not m:
        return None
    year = int(m.group(1))
    return {
        "target_form": "DEF 14A",
        "expected_year": year,
        "proxy_120_day_window": None,   # filled by caller using filing_date
        "resolved_accession": None,     # filled later by post-processing
    }
