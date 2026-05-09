"""Date helpers shared between the extractor pipeline and eval runners.

edgartools sometimes hands us a `date`, sometimes a `datetime`, sometimes an
ISO string, sometimes None; both consumers had been re-implementing the same
coercion. Centralised here so a future shape change updates one file.
"""

from __future__ import annotations

from datetime import date, datetime


def parse_iso_date(s: str | date | datetime | None) -> date:
    """Coerce edgartools' filing_date / period_of_report into a date.

    Falls back to date(1900,1,1) for None so downstream era checks can run
    without per-call None-guards. Truncates ISO strings at the date portion
    (10 chars) so an 'YYYY-MM-DDTHH:MM:SS' value still parses.
    """
    if s is None:
        return date(1900, 1, 1)
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    return date.fromisoformat(str(s)[:10])


def to_iso_string(d: date | datetime | str | None) -> str:
    """Inverse of parse_iso_date for serialising into ExtractionResult JSON."""
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.isoformat() if hasattr(d, "isoformat") else str(d)
