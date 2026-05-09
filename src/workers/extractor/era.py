"""Determine which 10-K items are applicable in a given filing era.

Reg S-K + form-amending rules introduced items at known dates:
- Item 1A Risk Factors:                  2005-12-01 (Securities Offering Reform)
- Item 1B Unresolved Staff Comments:     2005-12-01 (large accelerated filers)
- Item 1C Cybersecurity:                 fiscal year ending on/after 2023-12-15
- Item 9C HFCAA:                         2022-01-10
- Item 6 [Reserved]:                     2021 (post Reg S-K Selected Financial Data deletion)

This module helps decide whether a missing item is a parser error or
expected absence (`applicable_in_era=False`).
"""

from __future__ import annotations

from datetime import date

# All items by part, in regulation order
ALL_ITEMS_BY_PART: dict[int, list[str]] = {
    1: ["1", "1A", "1B", "1C", "2", "3", "4"],
    2: ["5", "6", "7", "7A", "8", "9", "9A", "9B", "9C"],
    3: ["10", "11", "12", "13", "14"],
    4: ["15", "16"],
}

# Items introduced after the form's original 1934 layout; keyed by item_number.
# `1C` uses `period_ending` cutoff (rule applies to fiscal years ending on/after);
# the others use `filing_date` cutoff (close-enough heuristic for the <2-week skew).
ITEM_INTRO: dict[str, date] = {
    "1A": date(2005, 12, 1),
    "1B": date(2005, 12, 1),
    "1C": date(2023, 12, 15),  # FY ending on/after, not filing date
    "9C": date(2022, 1, 10),
}


def items_applicable(filing_date: date, fiscal_year_end: date) -> list[str]:
    """Return the list of item_numbers that should appear in a 10-K with the
    given filing/fiscal dates, considering when each item was introduced.

    Items added in later years return absent for older filings, so a
    downstream classifier can mark them `applicable_in_era=False` instead
    of `not_applicable`.
    """
    out: list[str] = []
    for items in ALL_ITEMS_BY_PART.values():
        for item in items:
            intro = ITEM_INTRO.get(item)
            if intro is None:
                # always applicable
                out.append(item)
                continue
            if item == "1C":
                if fiscal_year_end >= intro:
                    out.append(item)
            else:
                if filing_date >= intro:
                    out.append(item)
    return out


def part_for_item(item_number: str) -> int:
    """Map item_number ('1', '1A', '9C', '15', etc.) to its Part (1-4).
    Returns 0 if unknown (e.g. 'cover' for a synthetic record)."""
    for part, items in ALL_ITEMS_BY_PART.items():
        if item_number in items:
            return part
    return 0
