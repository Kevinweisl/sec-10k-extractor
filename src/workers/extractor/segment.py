"""Segment a 10-K into items using edgartools' TenK.sections.

edgartools' `tk.sections` is a dict-like with keys like `part_i_item_1`,
`part_ii_item_7a` — these encode BOTH the part and the item_number, which is
what we need for the spec's output schema.

For ABS (Reg AB) 10-Ks, edgartools returns very few sections; we detect this
and mark the result as `is_abs_filing=True` so the caller can fall back to
`status=non_standard`.
"""

from __future__ import annotations

import re
from typing import Any

from edgar import Filing
from edgar.company_reports import TenK

from workers.extractor.era import part_for_item
from workers.extractor.regex_segment import edgartools_coverage_suspect, regex_segment


# section_key like "part_i_item_1a" -> ("1", "1A") with proper canonicalization
_PART_RX = re.compile(r"part_(?P<part>i{1,4})_item_(?P<item>\d+[a-z]?)", re.IGNORECASE)
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def _parse_section_key(key: str) -> tuple[int, str] | None:
    m = _PART_RX.fullmatch(key.strip())
    if not m:
        return None
    return _ROMAN[m.group("part").lower()], m.group("item").upper()


# Fallback for plain item names like "Item 1A" used in tk.items
_ITEM_RX = re.compile(r"^Item\s+(\d+[A-C]?)$", re.IGNORECASE)


def segment_with_edgartools(filing: Filing) -> dict[str, Any]:
    """Return {items: [...], is_abs_filing: bool, raw_text: str, raw_html: str | None}.

    Each item has: part, item_number, item_title, content_text.

    For canonical 10-Ks we iterate `tk.sections` (which gives part+item from
    the key). For ABS or other irregular filings, sections may be empty —
    we then iterate `tk.items` (item-only labels) and map back to part via
    the era.part_for_item helper.
    """
    tk: TenK = filing.obj()
    raw_text = _resolve(filing, "text") or ""
    raw_html = _resolve(filing, "html") or ""

    items: list[dict[str, Any]] = []
    seen_item_nums: set[str] = set()

    # Path 1: structured sections with part+item keys (the typical case)
    sections = getattr(tk, "sections", None)
    if sections:
        for key, value in sections.items():
            parsed = _parse_section_key(key)
            if not parsed:
                continue
            part, item_num = parsed
            text = _stringify_section(value)
            if not text:
                continue
            items.append({
                "part": part,
                "item_number": item_num,
                "item_title": _extract_title(text, item_num),
                "content_text": text,
            })
            seen_item_nums.add(item_num)

    # Path 2: fallback for items present in tk.items but missing from tk.sections.
    # On Apple 2024 we hit Items 15 + 16 this way (edgartools maps Part IV via tk['Item 15'] only).
    for label in getattr(tk, "items", []) or []:
        m = _ITEM_RX.match(label)
        if not m:
            continue
        item_num = m.group(1).upper()
        if item_num in seen_item_nums:
            continue
        try:
            text = _stringify_section(tk[label])
        except Exception:  # noqa: BLE001
            continue
        if not text:
            continue
        items.append({
            "part": part_for_item(item_num),
            "item_number": item_num,
            "item_title": _extract_title(text, item_num),
            "content_text": text,
        })
        seen_item_nums.add(item_num)

    is_abs = _looks_like_abs(items, filing)

    # Path 3: regex fallback when edgartools' coverage looks broken.
    # Triggers on filings like GE 2021 (cross-ref TOC) where edgartools yields
    # only 4 items with duplicate content. Skip for ABS filings — those are
    # genuinely non-standard, not just mis-parsed.
    used_regex_fallback = False
    if not is_abs and edgartools_coverage_suspect(items) and raw_text:
        regex_items = regex_segment(raw_text)
        if len(regex_items) > len(items):
            items = regex_items
            used_regex_fallback = True

    return {
        "items": items,
        "is_abs_filing": is_abs,
        "raw_text": raw_text,
        "raw_html": raw_html or "",
        "used_regex_fallback": used_regex_fallback,
    }


def _stringify_section(value: Any) -> str:
    """Extract plain text from a Section / str / other edgartools result, then
    trim any bleed-over into the next item.

    edgartools' section detection is approximate — page-break artifacts cause
    Item 9C's text to include the start of Item 10 (etc.). We truncate at the
    first occurrence of 'Item N(letter).' past the title to fix this."""
    raw = ""
    if value is None:
        raw = ""
    elif isinstance(value, str):
        raw = value
    else:
        text_attr = getattr(value, "text", None)
        if callable(text_attr):
            try:
                raw = text_attr() or ""
            except Exception:  # noqa: BLE001
                raw = ""
        elif isinstance(text_attr, str):
            raw = text_attr
        else:
            raw = str(value)
    return _trim_bleed(raw.strip())


# Find a "Item 1A." style heading. Used to detect bleed into the next item.
# Avoid `\b` before `Item` because edgartools' page-break artifacts produce
# run-on text like "PART IIItem 5." (no whitespace between II and Item) where
# `\b` does not match. Match plain `Item\s+\d+[A-C]?\.` anywhere.
_RE_BLEED = re.compile(r"Item\s+(\d+[A-C]?)\.", re.IGNORECASE)
# Also detect "PART II Item 5" / "PART IIItem 5" headers that often cause bleed
_RE_PART_HEADER = re.compile(r"PART\s+(I{1,4})\s*Item\s+\d+", re.IGNORECASE)


def _trim_bleed(text: str) -> str:
    """If `text` contains a second `Item N.` heading after the leading one,
    cut everything from that heading onward (it's the next item bleeding in)."""
    if not text:
        return text
    matches = list(_RE_BLEED.finditer(text))
    if len(matches) >= 2:
        # second match is the bleed — truncate there
        text = text[: matches[1].start()].rstrip()
    # Also clip at PART III / PART II header that prefixes Item 10 etc.
    pm = _RE_PART_HEADER.search(text)
    if pm and pm.start() > 100:  # skip if it's at the very beginning
        text = text[: pm.start()].rstrip()
    return text


def _extract_title(text: str, item_num: str) -> str:
    """Heuristic title from the first line:
       'Item 1A.  Risk Factors  (...content...)' -> 'Risk Factors'.

    edgartools uses non-breaking spaces (\xa0) as separators. Strip and
    take what's between 'Item N(letter)' and the next double-space chunk.
    """
    head = text.replace("\xa0", " ").lstrip()
    # Match e.g. 'Item 1A. Risk Factors '
    m = re.match(rf"item\s+{re.escape(item_num)}\.?\s*[-—:.]?\s*([^\n]{{0,120}}?)\s\s",
                 head, flags=re.IGNORECASE)
    if not m:
        # Try to grab up to ~80 chars of the first line as a fallback title
        first_line = head.split("\n", 1)[0]
        # remove the 'Item N' prefix
        first_line = re.sub(rf"^item\s+{re.escape(item_num)}\.?\s*",
                            "", first_line, flags=re.IGNORECASE)
        return first_line[:80].strip()
    return m.group(1).strip()


def _resolve(obj: Any, attr: str) -> Any:
    """edgartools mixes properties and methods; this calls callables and returns
    plain attributes uniformly."""
    if not hasattr(obj, attr):
        return None
    v = getattr(obj, attr)
    if callable(v):
        try:
            return v()
        except Exception:  # noqa: BLE001
            return None
    return v


def _looks_like_abs(items: list[dict[str, Any]], filing: Filing) -> bool:
    """Detect Asset-Backed Securities 10-K (Reg AB schema)."""
    text_l = (_resolve(filing, "text") or "")[:5000].lower()
    if "regulation ab" in text_l or "asset-backed" in text_l:
        return True
    # Empty-content check: if standard items 1, 2, 7, 8 are all missing,
    # this is almost certainly non-standard.
    standard = {it["item_number"] for it in items}
    if not ({"1", "2", "7", "8"} & standard):
        return True
    return False
