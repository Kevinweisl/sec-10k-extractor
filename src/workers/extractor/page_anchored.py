"""Body extraction fallback for cross-reference TOC filings.

When edgartools' main path returns only TOC stubs (Intel 2022-2026, GE 2021,
etc.), the section bodies actually exist in the raw text. We try two
recovery strategies:

  1. extract_by_page_anchors  — old-school SEC <a name="item_*"> anchors.
     Works on pre-2010-ish filings; modern iXBRL filings (Intel 2026 etc.)
     don't have these anchors any more.

  2. extract_by_title_headings — title-based body extraction. Modern Intel
     filings use topic titles like "Risk Factors", "Properties",
     "Cybersecurity" on their own lines in the body, then list "Item 1A.
     Risk Factors" only once in the cross-reference TOC at the back. We
     map titles to item numbers and slice the text between consecutive
     titles.

Pipeline tries (1), then (2), and uses whichever returns substantially
better content than the TOC-stub baseline.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from lxml import html as lxml_html
except ImportError:  # pragma: no cover — lxml is a hard dep
    lxml_html = None  # type: ignore[assignment]

# Canonical item→part mapping lives in era.py; reuse it here so any future
# SEC form changes (new items, restructured parts) only need one update.
from workers.extractor.era import part_for_item


# Anchor names follow several conventions across filings. SEC issues vary.
# Common patterns observed in Intel / GE / Apple HTMLs:
#   id="part_i"        name="part1"        id="part_I"
#   id="item_1"        name="item1"        id="item_1A"   name="item1a"
#   id="item1"         name="i1"
# We normalize on lowercase + strip non-alphanumerics for matching.
_ITEM_ANCHOR_RE = re.compile(
    r"(?:item)[_\-\s]*(\d+[a-c]?)\b",
    re.IGNORECASE,
)

_TOC_STUB_PREFIX = "[Cross-reference TOC]"


def is_toc_stub(content_text: str | None) -> bool:
    """Return True if the content is just a TOC line, not a real item body.

    Three signals:
      1. Explicit prefix that cross_reference_toc_segment writes
      2. Empty content (no body at all)
      3. Very short content (<200 chars) that contains a page-number hint
         like "Pages 37 - 51" — the cross-ref index pattern.
    """
    if not content_text:
        return True
    text = content_text.strip()
    if not text:
        return True  # whitespace-only counts as a stub
    if text.startswith(_TOC_STUB_PREFIX):
        return True
    if len(text) < 200 and re.search(r"\b(?:Pages?|page)\s+\d", text, re.IGNORECASE):
        return True
    return False


def toc_stub_rate(items: list[dict[str, Any]] | list[Any]) -> float:
    """Fraction of items whose content_text reads as a TOC stub."""
    if not items:
        return 0.0
    n = 0
    for it in items:
        # Support both dicts (segment output) and Item models (pipeline output).
        ct = it.get("content_text") if isinstance(it, dict) else getattr(it, "content_text", None)
        if is_toc_stub(ct):
            n += 1
    return round(n / len(items), 4)


def _item_sort_key(item_num: str) -> tuple[int, str]:
    """Sort key: ('1', '1A', '1B', '2', ..., '10', '11', ...)."""
    m = re.match(r"(\d+)([A-Z]?)", item_num.upper())
    if m:
        return (int(m.group(1)), m.group(2))
    return (9999, item_num)


def _collect_anchors(tree) -> dict[str, int]:
    """Find <a name="item_*"> and <a id="item_*"> across the document.

    Returns {item_number_upper: source_offset_int} where source_offset is the
    character position of the anchor in the rendered HTML text. Items with
    multiple anchors keep the FIRST occurrence (TOC links come second).
    """
    anchors: dict[str, int] = {}
    # We want the source character offset of each anchor so we can slice
    # the surrounding text. lxml gives us .sourceline (line-based) which is
    # coarse — instead we iterate elements and use their text_content position.
    body_text_so_far = 0
    for elem in tree.iter():
        # Check current element's attributes first
        for attr_name in ("name", "id"):
            attr = elem.get(attr_name, "")
            if not attr:
                continue
            m = _ITEM_ANCHOR_RE.search(attr)
            if m:
                item_num = m.group(1).upper()
                # First anchor wins (TOC links come after the body in most filings,
                # but body anchors typically appear in document order; safer to
                # take the SECOND occurrence which is the body itself, since the
                # FIRST tends to be the TOC link target). We collect both and
                # decide below.
                if item_num not in anchors:
                    anchors[item_num] = body_text_so_far
                else:
                    # Subsequent anchors: keep the later one if it's significantly
                    # further into the document — that's more likely the actual body.
                    if body_text_so_far - anchors[item_num] > 2000:
                        anchors[item_num] = body_text_so_far
        # Advance the text cursor
        if elem.text:
            body_text_so_far += len(elem.text)
        if elem.tail:
            body_text_so_far += len(elem.tail)
    return anchors


def extract_by_page_anchors(
    raw_html: str,
    existing_items: list[Any],
) -> list[dict[str, Any]] | None:
    """Slice raw_html using item anchors and populate per-item bodies.

    Returns a list of dicts shaped like the segment output, OR None if the
    HTML has no usable item anchors. The caller should compare avg content
    length before deciding to swap.

    Each returned dict carries:
      part, item_number, item_title, content_text, status_hint

    status_hint is left None (pipeline classifier decides).
    """
    if lxml_html is None or not raw_html:
        return None
    try:
        tree = lxml_html.fromstring(raw_html)
    except Exception:  # noqa: BLE001  malformed HTML — bail
        return None

    anchors = _collect_anchors(tree)
    if not anchors:
        return None

    # Get the full plain-text body once for slicing
    full_text = tree.text_content() or ""
    if not full_text:
        return None

    sorted_items = sorted(anchors.items(), key=lambda kv: _item_sort_key(kv[0]))
    existing_lookup = _build_existing_lookup(existing_items)

    result: list[dict[str, Any]] = []
    for i, (item_num, start_offset) in enumerate(sorted_items):
        # Determine slice end — start of next item anchor, or +200K chars cap
        next_start = (
            sorted_items[i + 1][1]
            if i + 1 < len(sorted_items)
            else min(len(full_text), start_offset + 200_000)
        )
        body = full_text[start_offset:next_start].strip()
        # Filter: only keep substantive bodies (avoid TOC-link slices < 400 chars)
        if len(body) < 400:
            continue
        result.append(_make_item_dict(item_num, body, existing_lookup))

    return result if result else None


# ---------------------------------------------------------------------------
# Shared helpers used by both extract_by_page_anchors and
# extract_by_title_headings. Centralising them avoids the copy-paste both
# functions used to carry (existing-lookup build + per-item dict assembly).

def _build_existing_lookup(existing_items: list[Any]) -> dict[str, Any]:
    """Map item_number → original item (dict or model) for title/part reuse."""
    lookup: dict[str, Any] = {}
    for it in existing_items:
        num = it.get("item_number") if isinstance(it, dict) else getattr(it, "item_number", None)
        if num:
            lookup[str(num).upper()] = it
    return lookup


def _get_existing_attrs(existing: Any) -> tuple[str, int]:
    """Return (title, part) from an existing item, supporting dict or model."""
    if existing is None:
        return ("", 0)
    if isinstance(existing, dict):
        return (existing.get("item_title", "") or "", existing.get("part", 0) or 0)
    return (
        getattr(existing, "item_title", "") or "",
        getattr(existing, "part", 0) or 0,
    )


def _make_item_dict(
    item_num: str,
    body: str,
    existing_lookup: dict[str, Any],
) -> dict[str, Any]:
    """Build the segment-shaped dict for one recovered item.

    Uses era.part_for_item for part inference (single source of truth) and
    falls back to _default_title when the original segmentation did not
    provide a title for this item.
    """
    existing_title, existing_part = _get_existing_attrs(existing_lookup.get(item_num))
    return {
        "part": existing_part or part_for_item(item_num),
        "item_number": item_num,
        "item_title": existing_title or _default_title(item_num),
        "content_text": body,
        "status_hint": None,  # let classifier decide
    }


_DEFAULT_TITLES = {
    "1":  "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2":  "Properties",
    "3":  "Legal Proceedings",
    "4":  "Mine Safety Disclosures",
    "5":  "Market for Registrant's Common Equity",
    "6":  "[Reserved]",
    "7":  "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8":  "Financial Statements and Supplementary Data",
    "9":  "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions",
    "10": "Directors, Executive Officers, and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}


def _default_title(item_num: str) -> str:
    return _DEFAULT_TITLES.get(item_num.upper(), f"Item {item_num}")


# ============================================================================
# Title-based extraction (for modern iXBRL filings without item anchors)
# ============================================================================
#
# Modern Intel-style filings have the cross-reference TOC at the end with
# "Item 1A. Risk Factors" entries, but the body sections themselves use bare
# topic titles like "Risk Factors", "Properties", "Cybersecurity" each on its
# own line. We map (title pattern) -> (item_number) and slice the text
# between consecutive title matches.
#
# Patterns are case-insensitive multiline, anchored to start of a stripped
# line so we don't match the same word mid-paragraph.

# Each tuple is (item_number, list_of_title_aliases). Aliases ordered most
# specific first so "Management's Discussion" matches before "Management"
# alone (which would be ambiguous).
_BODY_TITLE_MAP: list[tuple[str, list[str]]] = [
    ("1",  [r"Business"]),
    ("1A", [r"Risk Factors"]),
    ("1B", [r"Unresolved Staff Comments"]),
    ("1C", [r"Cybersecurity"]),
    ("2",  [r"Properties"]),
    ("3",  [r"Legal Proceedings"]),
    ("4",  [r"Mine Safety Disclosures"]),
    ("5",  [r"Market for Registrant['’]s Common Equity[^\n]{0,140}",
            r"Market for Our Common Equity[^\n]{0,140}"]),
    ("6",  [r"\[Reserved\]", r"Selected Financial Data"]),
    ("7",  [r"Management['’]s Discussion and Analysis[^\n]{0,140}"]),
    ("7A", [r"Quantitative and Qualitative Disclosures About Market Risk"]),
    ("8",  [r"Financial Statements and Supplement(?:ary|al)\s+(?:Data|Details)[^\n]{0,40}",
            r"Financial Statements and Supplementary Data"]),
    ("9",  [r"Changes in and Disagreements with Accountants[^\n]{0,140}"]),
    ("9A", [r"Controls and Procedures"]),
    ("9B", [r"Other Information"]),
    ("9C", [r"Disclosure Regarding Foreign Jurisdictions[^\n]{0,140}"]),
    ("10", [r"Directors, Executive Officers,?\s+and Corporate Governance"]),
    ("11", [r"Executive Compensation"]),
    ("12", [r"Security Ownership of Certain Beneficial Owners[^\n]{0,140}"]),
    ("13", [r"Certain Relationships and Related Transactions[^\n]{0,140}"]),
    ("14", [r"Principal Accountant(?:\s+Fees and Services)?"]),
    ("15", [r"Exhibits(?:,? and Financial Statement Schedules)?"]),
    ("16", [r"Form 10-K Summary"]),
]


def _build_title_regex() -> list[tuple[str, re.Pattern[str]]]:
    """Compile one regex per item. Each looks for the title on its own line
    (allowing leading/trailing whitespace) so we don't match a paragraph
    containing the same words mid-sentence.
    """
    compiled = []
    for item_num, aliases in _BODY_TITLE_MAP:
        # Match: (start-of-line, optional whitespace) ALIAS (optional
        # whitespace, end-of-line). We use \r?\n boundaries explicitly so
        # the multi-line behaviour is reliable across edgartools text output.
        alt = "|".join(f"(?:{a})" for a in aliases)
        pat = re.compile(
            r"(?:^|\n)[ \t\xa0]{0,8}(?:" + alt + r")[ \t\xa0]*(?:\n|$)",
            re.IGNORECASE,
        )
        compiled.append((item_num, pat))
    return compiled


_TITLE_REGEX_CACHE: list[tuple[str, re.Pattern[str]]] | None = None


def _title_regexes() -> list[tuple[str, re.Pattern[str]]]:
    global _TITLE_REGEX_CACHE  # noqa: PLW0603
    if _TITLE_REGEX_CACHE is None:
        _TITLE_REGEX_CACHE = _build_title_regex()
    return _TITLE_REGEX_CACHE


# Some filings put the cross-reference TOC at the END of raw_text. The TOC
# itself contains all item titles in close succession, which would confuse
# our title extractor (the TOC line entries are not section bodies). We trim
# the trailing TOC region when we detect it.
_TOC_END_MARKER = re.compile(
    r"(?im)\bCROSS[ \t\xa0-]+REFERENCE[ \t\xa0]+INDEX",
)


def _strip_trailing_toc(raw_text: str) -> str:
    """If a 'CROSS REFERENCE INDEX' header appears in the last 20% of the
    document, drop everything from that point on.
    """
    if not raw_text:
        return raw_text
    cutoff = int(len(raw_text) * 0.80)
    m = _TOC_END_MARKER.search(raw_text)
    if m and m.start() >= cutoff:
        return raw_text[:m.start()]
    return raw_text


def extract_by_title_headings(
    raw_text: str,
    existing_items: list[Any],
) -> list[dict[str, Any]] | None:
    """Slice raw_text into item bodies by matching standardized SEC titles.

    Returns a list of dicts (same shape as segment output), or None if too
    few title matches were found.
    """
    if not raw_text:
        return None
    body_text = _strip_trailing_toc(raw_text)
    if not body_text:
        return None

    # First match per item_number wins. (Some titles like "Financial
    # Statements" appear repeatedly inside Item 8 itself; we only want the
    # outermost section start.)
    found: dict[str, int] = {}
    for item_num, pat in _title_regexes():
        m = pat.search(body_text)
        if m and item_num not in found:
            # Use match-text-start (skip the leading newline + whitespace)
            text_start = m.start() + (1 if body_text[m.start()] == "\n" else 0)
            found[item_num] = text_start

    if len(found) < 4:
        # Not enough title hits — fall back to whatever the caller had.
        return None

    existing_lookup = _build_existing_lookup(existing_items)

    # Sort items by document position and slice between consecutive matches.
    sorted_items = sorted(found.items(), key=lambda kv: kv[1])
    result: list[dict[str, Any]] = []
    for i, (item_num, start_pos) in enumerate(sorted_items):
        end_pos = (
            sorted_items[i + 1][1]
            if i + 1 < len(sorted_items)
            else len(body_text)
        )
        body = body_text[start_pos:end_pos].strip()
        if len(body) < 200:
            # Too short to be a real section body. Likely a false match.
            continue
        result.append(_make_item_dict(item_num, body, existing_lookup))
    return result if result else None
