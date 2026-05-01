"""Char-range IoU (Intersection over Union) — the eval metric for alignment quality.

Used by the eval runner (D3-8) to compare predicted char_range_text against
gold-annotated ranges. IoU=1.0 means perfect overlap; IoU=0.0 means no overlap.

A small IoU under whitespace-only differences is acceptable; a small IoU when
the segments are entirely off is a real failure.
"""

from __future__ import annotations


Span = tuple[int, int]


def compute_iou(a: Span | None, b: Span | None) -> float:
    """Return IoU of two (start, end) char ranges.

    IoU = |intersection| / |union|.

    Edge cases:
      - Either span is None         → 0.0 (a missing prediction can't overlap)
      - Both spans are zero-length  → 1.0 if equal, else 0.0
      - Negative-length spans       → treated as 0.0 (invalid)
    """
    if a is None or b is None:
        return 0.0
    a0, a1 = a
    b0, b1 = b
    if a1 < a0 or b1 < b0:
        return 0.0

    # Zero-length comparison: only equal points overlap perfectly.
    if a0 == a1 and b0 == b1:
        return 1.0 if a0 == b0 else 0.0

    inter = max(0, min(a1, b1) - max(a0, b0))
    union = (a1 - a0) + (b1 - b0) - inter
    return inter / union if union > 0 else 0.0


def mean_iou(pairs: list[tuple[Span | None, Span | None]]) -> float:
    """Mean IoU across multiple (predicted, gold) pairs.

    Empty list returns 0.0.
    """
    if not pairs:
        return 0.0
    return sum(compute_iou(p, g) for p, g in pairs) / len(pairs)
