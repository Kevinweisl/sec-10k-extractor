"""Regex-based fallback segmenter — used when edgartools mis-segments a 10-K.

edgartools is right ~95% of the time on modern 10-Ks but fails on filings with
unusual TOC structures (e.g. GE 2021 cross-ref TOC) or pre-iXBRL eras (e.g.
Chemical Banking 1995 SGML). For those, we fall back to a regex sweep over
the plain text:

  1. Find every "Item N(letter)." heading at line start.
  2. Filter out TOC matches by requiring the heading to be followed by enough
     content (≥200 chars before the next heading) — a TOC entry typically has
     the title and then jumps straight to the next entry.
  3. Slice text between consecutive headings.
  4. Map each item_number to its part via era.part_for_item.

This module is a fallback, not a replacement. The pipeline calls it only when
edgartools' coverage is suspiciously low (< 8 items for a modern 10-K) or when
the same content appears under multiple item numbers (a known edgartools
duplication mode).
"""

from __future__ import annotations

import re
from typing import Any

from workers.extractor.era import part_for_item

# Match "Item 1A." or "ITEM 7." at start of a line, allowing 0-3 leading
# whitespace chars. Captures the item number including optional A/B/C suffix.
_RE_ITEM_HEADING = re.compile(
    r"(?im)^[ \t\xa0]{0,3}(?:Item|ITEM)[ \t\xa0]+(\d+[A-Ca-c]?)[\s\.\-—:]+([^\n]{0,200})$"
)

# Minimum chars between two heading positions to count as a real Item body
# rather than a TOC entry. 10-K Items vary wildly in size (Item 6 = "[Reserved]"
# at 12 chars, Item 1A = 50K chars), so this threshold is for distinguishing
# TOC from body, not body from non-body.
_MIN_BODY_CHARS = 200


# Title-based heading detection — fallback for filings like GE 2021 where the
# body uses ALL-CAPS section titles (e.g. "RISK FACTORS.") and the "Item N."
# labels only appear in a cross-reference TOC at the document end.
#
# These titles are stable across registrants per the SEC's standard 10-K form.
# Each entry: (regex pattern matching the heading, item_number). Patterns are
# anchored to line start. Each pattern requires the title be followed by a
# terminator (`.`, `:`, dash) plus whitespace OR end-of-line — distinguishes a
# heading from prose mentioning the same words. Case-insensitive at compile time.
_TITLE_TERM = r"(?:[\.\:\-—][ \t\xa0]+|[ \t\xa0]*$)"
_TITLE_HEADINGS: tuple[tuple[str, str], ...] = (
    # Part I — order matters where prefixes overlap
    (r"^[ \t\xa0]{0,3}BUSINESS" + _TITLE_TERM, "1"),
    (r"^[ \t\xa0]{0,3}RISK[ \t\xa0]+FACTORS" + _TITLE_TERM, "1A"),
    (r"^[ \t\xa0]{0,3}UNRESOLVED[ \t\xa0]+STAFF[ \t\xa0]+COMMENTS" + _TITLE_TERM, "1B"),
    (r"^[ \t\xa0]{0,3}CYBERSECURITY" + _TITLE_TERM, "1C"),
    (r"^[ \t\xa0]{0,3}PROPERTIES" + _TITLE_TERM, "2"),
    (r"^[ \t\xa0]{0,3}LEGAL[ \t\xa0]+PROCEEDINGS" + _TITLE_TERM, "3"),
    (r"^[ \t\xa0]{0,3}MINE[ \t\xa0]+SAFETY[ \t\xa0]+DISCLOSURES" + _TITLE_TERM, "4"),
    # Part II
    (r"^[ \t\xa0]{0,3}MARKET[ \t\xa0]+FOR[ \t\xa0]+(?:THE[ \t\xa0]+)?(?:REGISTRANT['']S[ \t\xa0]+)?COMMON[ \t\xa0]+EQUITY", "5"),
    (r"^[ \t\xa0]{0,3}SELECTED[ \t\xa0]+FINANCIAL[ \t\xa0]+DATA" + _TITLE_TERM, "6"),
    (r"^[ \t\xa0]{0,3}\[?[ \t\xa0]*RESERVED[ \t\xa0]*\]?" + _TITLE_TERM, "6"),
    (r"^[ \t\xa0]{0,3}MANAGEMENT['']?S[ \t\xa0]+DISCUSSION[ \t\xa0]+AND[ \t\xa0]+ANALYSIS", "7"),
    (r"^[ \t\xa0]{0,3}QUANTITATIVE[ \t\xa0]+AND[ \t\xa0]+QUALITATIVE[ \t\xa0]+DISCLOSURES", "7A"),
    (r"^[ \t\xa0]{0,3}FINANCIAL[ \t\xa0]+STATEMENTS[ \t\xa0]+AND[ \t\xa0]+SUPPLEMENTARY[ \t\xa0]+DATA", "8"),
    (r"^[ \t\xa0]{0,3}CHANGES[ \t\xa0]+IN[ \t\xa0]+AND[ \t\xa0]+DISAGREEMENTS[ \t\xa0]+WITH[ \t\xa0]+ACCOUNTANTS", "9"),
    (r"^[ \t\xa0]{0,3}CONTROLS[ \t\xa0]+AND[ \t\xa0]+PROCEDURES" + _TITLE_TERM, "9A"),
    (r"^[ \t\xa0]{0,3}OTHER[ \t\xa0]+INFORMATION" + _TITLE_TERM, "9B"),
    (r"^[ \t\xa0]{0,3}DISCLOSURE[ \t\xa0]+REGARDING[ \t\xa0]+FOREIGN[ \t\xa0]+JURISDICTIONS", "9C"),
    # Part III
    (r"^[ \t\xa0]{0,3}DIRECTORS[, ]+EXECUTIVE[ \t\xa0]+OFFICERS", "10"),
    (r"^[ \t\xa0]{0,3}EXECUTIVE[ \t\xa0]+COMPENSATION" + _TITLE_TERM, "11"),
    (r"^[ \t\xa0]{0,3}SECURITY[ \t\xa0]+OWNERSHIP[ \t\xa0]+OF[ \t\xa0]+CERTAIN", "12"),
    (r"^[ \t\xa0]{0,3}CERTAIN[ \t\xa0]+RELATIONSHIPS[ \t\xa0]+AND[ \t\xa0]+RELATED", "13"),
    (r"^[ \t\xa0]{0,3}PRINCIPAL[ \t\xa0]+ACCOUNTANT(?:ANT)?[ \t\xa0]+FEES", "14"),
    # Part IV
    (r"^[ \t\xa0]{0,3}EXHIBITS?[ \t\xa0]*(?:AND|,)[ \t\xa0]*FINANCIAL[ \t\xa0]+STATEMENT[ \t\xa0]+SCHEDULES", "15"),
    (r"^[ \t\xa0]{0,3}FORM[ \t\xa0]+10-?K[ \t\xa0]+SUMMARY" + _TITLE_TERM, "16"),
)

_COMPILED_TITLE_HEADINGS = tuple(
    (re.compile(pat, re.IGNORECASE | re.MULTILINE), num) for pat, num in _TITLE_HEADINGS
)


def regex_segment(raw_text: str) -> list[dict[str, Any]]:
    """Slice raw_text into items by 'Item N.' headings, with title-based fallback.

    Returns a list of {part, item_number, item_title, content_text}. Empty
    list if no headings found (e.g. binary or non-text input).

    Strategy:
      1. Try "Item N." headings first — works for typical 10-Ks.
      2. If that yields < 8 items (or 0), try title-based headings — recovers
         filings like GE 2021 where the body uses ALL-CAPS section titles and
         "Item N." appears only in a cross-reference TOC at the document end.
    """
    if not raw_text:
        return []

    # Cross-reference TOC filings (e.g. GE 2021) — when a "CROSS REFERENCE
    # INDEX" header is present, prefer the TOC parser over heading detection.
    # The TOC is the only place the filing canonically declares each item's
    # status (page-range / Not applicable / by-reference footnote), so we lose
    # critical signal if we use the heading-based path even though it'd find
    # the same item_numbers.
    toc_items = cross_reference_toc_segment(raw_text)
    if toc_items and len(toc_items) >= 8:
        return toc_items

    items = _segment_by_item_headings(raw_text)
    if len(items) >= 8:
        return items

    title_items = _segment_by_titles(raw_text)
    candidates = [c for c in (items, toc_items or [], title_items) if c]
    return max(candidates, key=len) if candidates else []


# Valid 10-K item numbers per SEC Form 10-K. Anything outside this set
# (e.g. "Item 405" from a 10-K405 reference, "Item 13(a)" sub-items) is a
# false match and gets filtered.
_VALID_ITEM_NUMBERS = frozenset({
    "1", "1A", "1B", "1C", "2", "3", "4",
    "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
    "10", "11", "12", "13", "14",
    "15", "16",
})


def _segment_by_item_headings(raw_text: str) -> list[dict[str, Any]]:
    """Slice raw_text by 'Item N.' line-start headings.

    Dedup strategy: group all matches by item_number. For each item_number,
    pick the occurrence with the most body-content following (until the
    next heading of any kind). This correctly disambiguates TOC entry
    (small body distance) from body anchor (larger body distance), even
    when multiple items have short bodies clustered together (e.g.
    Chemical Banking 1995 where Items 10/11/12 all say "See Item 13 below"
    in 100-char paragraphs).
    """
    raw_headings = list(_RE_ITEM_HEADING.finditer(raw_text))
    headings = [m for m in raw_headings if m.group(1).upper() in _VALID_ITEM_NUMBERS]
    if not headings:
        return []

    # All heading positions (sorted) — used to compute "distance to next heading"
    all_starts = sorted(m.start() for m in headings)

    # Group by item_number
    by_num: dict[str, list[re.Match[str]]] = {}
    for m in headings:
        by_num.setdefault(m.group(1).upper(), []).append(m)

    # For each item_number with multiple occurrences, pick the one with the
    # largest distance to the next heading (= most body content). Ties broken
    # by later document position (body usually comes after TOC).
    picks: list[re.Match[str]] = []
    for num, matches in by_num.items():
        best = max(
            matches,
            key=lambda m: (_distance_to_next_heading(m, all_starts), m.start()),
        )
        picks.append(best)

    picks.sort(key=lambda m: m.start())

    items: list[dict[str, Any]] = []
    for i, m in enumerate(picks):
        item_num = m.group(1).upper()
        title = m.group(2).strip().rstrip(".").strip()
        body_end = picks[i + 1].start() if i + 1 < len(picks) else len(raw_text)
        content_text = raw_text[m.start():body_end].strip()
        if len(content_text) < 30:
            continue
        items.append({
            "part": part_for_item(item_num),
            "item_number": item_num,
            "item_title": title[:120],
            "content_text": content_text,
        })
    return items


def _distance_to_next_heading(m: "re.Match[str]", all_starts: list[int]) -> int:
    """Distance from `m`'s end to the next heading position in all_starts."""
    pos = m.start()
    # binary search would be O(log n); list is small enough that linear is fine
    for s in all_starts:
        if s > pos:
            return s - pos
    return 10**9  # last heading — effectively infinite room


def _segment_by_titles(raw_text: str) -> list[dict[str, Any]]:
    """Slice raw_text by standard 10-K item titles (e.g. 'RISK FACTORS').

    Used for filings where the body uses title-only headings rather than
    'Item N.' labels. The titles in `_TITLE_HEADINGS` are SEC-form-stable
    across registrants, so this works even if the registrant doesn't repeat
    'Item 1A.' in the body.
    """
    # Find every title heading occurrence; tag with item_number.
    found: list[tuple[int, int, str, str]] = []  # (start, end, item_num, title_text)
    for compiled, item_num in _COMPILED_TITLE_HEADINGS:
        for m in compiled.finditer(raw_text):
            title_text = m.group(0).strip()
            found.append((m.start(), m.end(), item_num, title_text))

    if not found:
        return []
    # Sort by position
    found.sort(key=lambda x: x[0])

    # Dedup: when a title appears multiple times, keep the LATEST occurrence
    # that has substantial body after it. TOC entries cluster early; body
    # entries are spread out. Group adjacent same-item entries.
    deduped: list[tuple[int, int, str, str]] = []
    seen_position_per_item: dict[str, int] = {}
    for entry in found:
        start, end, item_num, title = entry
        # If we've already kept this item_num and the previous entry is
        # close (within MIN_BODY_CHARS), replace it. Otherwise append.
        if item_num in seen_position_per_item:
            prev_idx = seen_position_per_item[item_num]
            prev_start = deduped[prev_idx][0]
            if start - prev_start < _MIN_BODY_CHARS:
                deduped[prev_idx] = entry
                continue
            # Far apart — both could be valid; take the later one
            deduped[prev_idx] = entry
            continue
        deduped.append(entry)
        seen_position_per_item[item_num] = len(deduped) - 1

    # Sort by position again post-dedup
    deduped.sort(key=lambda x: x[0])

    # Slice between consecutive headings
    items: list[dict[str, Any]] = []
    for i, (start, end, item_num, title) in enumerate(deduped):
        body_end = deduped[i + 1][0] if i + 1 < len(deduped) else len(raw_text)
        content_text = raw_text[start:body_end].strip()
        if len(content_text) < 30:
            continue
        items.append({
            "part": part_for_item(item_num),
            "item_number": item_num,
            "item_title": title[:120],
            "content_text": content_text,
        })
    return items


# SEC permits a "cross-reference index" filing format (GE 2021, some big
# conglomerates). The body is organized topically, not by Item, and a TOC at
# the document end maps each Item N to a page range / "Not applicable" /
# footnote letter pointing at the proxy.
_RE_CROSS_REF_HEADER = re.compile(
    r"(?im)^[ \t\xa0]*(?:FORM\s+10-?K\s+)?CROSS[ \t\xa0\-]+REFERENCE[ \t\xa0]+INDEX",
)
_RE_CROSS_REF_LINE = re.compile(
    r"(?m)^[ \t\xa0]{0,5}Item[ \t\xa0]+(\d+[A-Ca-c]?)[\s\.\-—:]+([^\n]{0,250})$",
    re.IGNORECASE,
)


def cross_reference_toc_segment(raw_text: str) -> list[dict[str, Any]] | None:
    """If raw_text contains a 'CROSS REFERENCE INDEX' at the end, parse it
    and return a synthesized list of items based on the index entries.

    Returns None if no cross-reference index is detected. Otherwise returns
    items with `status_hint` field that the classifier can use:
      - "not applicable" / "none" / "n/a"   → status_hint='not_applicable'
      - "(a)", "(b)" footnote references    → status_hint='incorporated_by_reference'
      - page range like "5-43" or "44"      → status_hint='extracted'
    """
    header_match = _RE_CROSS_REF_HEADER.search(raw_text)
    if not header_match:
        return None
    toc_region = raw_text[header_match.end():]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in _RE_CROSS_REF_LINE.finditer(toc_region):
        item_num = m.group(1).upper()
        if item_num in seen:
            continue
        seen.add(item_num)
        line = m.group(2).strip()
        # Split "Title  PageRef" — typically separated by 2+ spaces
        title, page_ref = _split_toc_line(line)
        status_hint = _classify_toc_page_ref(page_ref)
        items.append({
            "part": part_for_item(item_num),
            "item_number": item_num,
            "item_title": title[:120],
            # We can't extract per-item body for cross-ref filings via regex;
            # report the TOC entry as content_text and let the eval flag it.
            "content_text": f"[Cross-reference TOC] {title} ({page_ref})".strip(),
            "status_hint": status_hint,
        })
    return items if items else None


_RE_TOC_SPLIT = re.compile(r"\s{2,}|\t+|\xa0{2,}")


def _split_toc_line(line: str) -> tuple[str, str]:
    """Split a TOC line into (title, page_ref). Heuristic: split on 2+ spaces."""
    parts = _RE_TOC_SPLIT.split(line.strip())
    parts = [p for p in parts if p.strip()]
    if not parts:
        return ("", "")
    if len(parts) == 1:
        return (parts[0], "")
    return (" ".join(parts[:-1]).strip(), parts[-1].strip())


def _classify_toc_page_ref(page_ref: str) -> str:
    """Map a TOC page reference to a status hint."""
    if not page_ref:
        return "extracted"
    s = page_ref.strip().lower()
    if "not applicable" in s or s == "none" or s == "n/a":
        return "not_applicable"
    # "(a)", "(b)", "(c)" — footnote letters typically refer to proxy by-ref
    if re.fullmatch(r"\([a-z]\)(?:[, ]+\d+)*", s):
        return "incorporated_by_reference"
    # any digits → page reference, body content exists
    if re.search(r"\d", s):
        return "extracted"
    return "extracted"


def edgartools_coverage_suspect(items: list[dict[str, Any]]) -> bool:
    """Heuristic: should we fall back to regex segmentation?

    Triggers when:
      - very few items (< 8 — a normal 10-K has at least 14 standard items)
      - duplicate content across different item_numbers
        (edgartools' GE 2021 mode where Item 1 and Item 1A share text)
    """
    if len(items) < 8:
        return True
    seen_content: dict[str, str] = {}
    for it in items:
        content = (it.get("content_text") or "").strip()
        if not content:
            continue
        key = content[:500]
        if key in seen_content and seen_content[key] != it["item_number"]:
            return True
        seen_content[key] = it["item_number"]
    return False
