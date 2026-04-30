"""CLI smoke test: extract one 10-K and print the result JSON.

Usage:
  python scripts/run_one_extract.py 320193 0000320193-24-000123
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from workers.extractor.pipeline import extract_10k


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_one_extract.py <CIK> <ACCESSION>")
        return 2
    cik, accession = sys.argv[1], sys.argv[2]
    result = extract_10k(cik, accession)
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
