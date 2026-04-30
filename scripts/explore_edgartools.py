"""Probe edgartools' real API on Apple 2024 10-K.

We need to know:
1. Does edgartools fetch the filing itself, or do we need a separate fetcher?
2. What's the segmentation API? (`tk.sections`? `tk['Item 1A']`? something else?)
3. What metadata does it expose (filing_date, primary_document, period)?
4. How does it handle SGML-era filings (pre-HTML, e.g. Chemical Banking 1995)?
5. Does it expose the raw HTML/text source for char_range alignment?

Run: `python scripts/explore_edgartools.py`
Cache: edgartools writes to ~/.edgar/.
"""

from __future__ import annotations

import json
import os
import sys
from pprint import pprint

# edgartools needs identity for SEC requests
os.environ.setdefault(
    "EDGAR_IDENTITY",
    "Kevin Wei interview-hw-2026 weisl@nlg.csie.ntu.edu.tw",
)

from edgar import Filing, set_identity  # noqa: E402

set_identity(os.environ["EDGAR_IDENTITY"])

# Apple 2024 10-K
APPLE_2024_ACCESSION = "0000320193-24-000123"


def explore_apple():
    print(f"=== Apple 2024 10-K (accession={APPLE_2024_ACCESSION}) ===")
    filing = Filing(form="10-K", accession_no=APPLE_2024_ACCESSION,
                    cik=320193, filing_date="2024-11-01",
                    company="Apple Inc.")
    print(f"Filing repr: {filing}")
    print(f"Filing dir: {[a for a in dir(filing) if not a.startswith('_')]}")

    # Get the TenK object
    print("\n--- filing.obj() ---")
    try:
        tk = filing.obj()
        print(f"TenK type: {type(tk).__name__}")
        print(f"TenK dir: {[a for a in dir(tk) if not a.startswith('_')]}")
    except Exception as exc:  # noqa: BLE001
        print(f"filing.obj() failed: {exc}")
        return

    # Try common accessors
    print("\n--- Try .items / .sections / dict access ---")
    for attr in ["items", "sections", "_items", "_sections", "__getitem__"]:
        if hasattr(tk, attr):
            v = getattr(tk, attr)
            print(f"  has attr {attr!r}: type={type(v).__name__}")

    # Try named properties
    print("\n--- Try named section properties ---")
    for attr in ["business", "business_description", "risk_factors", "mda",
                 "management_discussion", "properties", "legal_proceedings",
                 "financial_statements", "exhibits"]:
        if hasattr(tk, attr):
            try:
                v = getattr(tk, attr)
                if v is not None:
                    snippet = str(v)[:120].replace("\n", " ")
                    print(f"  {attr}: ({type(v).__name__}) {snippet!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {attr}: ERROR {exc}")

    # Try dict-style
    print("\n--- Try tk['Item N'] ---")
    for key in ["Item 1", "Item 1A", "Item 1B", "Item 1C", "Item 6",
                "Item 9C", "Item 11", "Item 15"]:
        try:
            v = tk[key]  # may raise
            snippet = str(v)[:200].replace("\n", " ")
            print(f"  {key!r}: ({type(v).__name__}) {snippet!r}")
        except (KeyError, TypeError) as exc:
            print(f"  {key!r}: NOT subscriptable / KeyError ({exc})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {key!r}: ERROR {exc}")

    # Raw text / HTML access?
    print("\n--- Raw text / HTML access ---")
    for attr in ["text", "html", "content", "raw", "document"]:
        if hasattr(filing, attr):
            try:
                v = getattr(filing, attr)
                if callable(v):
                    v = v()
                if v:
                    print(f"  filing.{attr}: type={type(v).__name__}, len≈{len(str(v))}")
            except Exception as exc:  # noqa: BLE001
                print(f"  filing.{attr}: ERROR {exc}")
    for attr in ["text", "html", "content"]:
        if hasattr(tk, attr):
            try:
                v = getattr(tk, attr)
                if callable(v):
                    v = v()
                if v:
                    print(f"  tk.{attr}: type={type(v).__name__}, len≈{len(str(v))}")
            except Exception as exc:  # noqa: BLE001
                print(f"  tk.{attr}: ERROR {exc}")


if __name__ == "__main__":
    try:
        explore_apple()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)
