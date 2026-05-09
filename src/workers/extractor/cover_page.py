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

from workers.extractor.schema import ReferencedFiling

# Year regex restricted to plausible filing-cycle range. Plain `\d{4}` would
# match "Securities Act of 1933" or stray IDs ahead of the actual year.
_RE_BLOCK = re.compile(
    r"DOCUMENTS\s+INCORPORATED\s+BY\s+REFERENCE\b.{0,2000}?"
    r"(?P<year>19[89]\d|20\d{2})\s+(?:annual\s+meeting|proxy\s+statement|definitive\s+proxy)",
    re.IGNORECASE | re.DOTALL,
)


def detect_cover_incorporates(text: str) -> ReferencedFiling | None:
    """Return a ReferencedFiling for the cover-page proxy reference, or None."""
    m = _RE_BLOCK.search(text)
    if not m:
        return None
    return ReferencedFiling(
        target_form="DEF 14A",
        expected_year=int(m.group("year")),
    )
