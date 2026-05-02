"""Entry point: `python -m workers.extractor`"""

from typing import Any

from workers.base import run_worker
from workers.extractor.pipeline import extract_10k


async def sec_extract_10k_handler(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap the synchronous extract_10k pipeline as an async handler.

    Input shape: {cik, accession, xbrl_validate?: bool, enable_llm_aug?: bool}
    """
    cik = payload.get("cik")
    accession = payload.get("accession")
    if cik is None or accession is None:
        raise ValueError("sec-extract-10k requires both 'cik' and 'accession'")

    result = extract_10k(
        cik=cik,
        accession=accession,
        xbrl_validate=bool(payload.get("xbrl_validate", True)),
        enable_llm_aug=bool(payload.get("enable_llm_aug", False)),
    )
    return result.model_dump(mode="json")


HANDLERS = {
    "sec-extract-10k": sec_extract_10k_handler,
}


if __name__ == "__main__":
    run_worker(target="extractor", handlers=HANDLERS)
