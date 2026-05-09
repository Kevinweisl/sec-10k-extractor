"""Tests for char-range IoU utility."""

from workers.extractor.iou import compute_iou, mean_iou


def test_iou_identical():
    assert compute_iou((100, 200), (100, 200)) == 1.0


def test_iou_disjoint_left():
    assert compute_iou((0, 50), (100, 200)) == 0.0


def test_iou_disjoint_right():
    assert compute_iou((300, 400), (100, 200)) == 0.0


def test_iou_full_overlap():
    # a fully contains b
    iou = compute_iou((0, 200), (50, 100))
    # intersection = 50, union = 200
    assert iou == 0.25


def test_iou_partial_overlap_half():
    # a=(0,100), b=(50,150) → inter=50, union=150, iou=1/3
    iou = compute_iou((0, 100), (50, 150))
    assert abs(iou - 1 / 3) < 1e-9


def test_iou_touching_no_overlap():
    # boundary touch (end == start) is 0 overlap
    assert compute_iou((0, 100), (100, 200)) == 0.0


def test_iou_none_returns_zero():
    assert compute_iou(None, (0, 100)) == 0.0
    assert compute_iou((0, 100), None) == 0.0
    assert compute_iou(None, None) == 0.0


def test_iou_invalid_negative_span():
    assert compute_iou((100, 50), (100, 200)) == 0.0


def test_iou_zero_length_equal():
    assert compute_iou((100, 100), (100, 100)) == 1.0


def test_iou_zero_length_different():
    assert compute_iou((100, 100), (200, 200)) == 0.0


def test_iou_whitespace_drift_high_iou():
    # Realistic: predicted vs gold differ by 10 chars at start, 5 at end
    # gold = (1000, 5000) → length 4000
    # pred = (1010, 5005) → length 3995
    # intersection = (1010, 5000) → 3990
    # union = 4000 + 3995 - 3990 = 4005
    iou = compute_iou((1010, 5005), (1000, 5000))
    assert iou > 0.99


def test_mean_iou_empty():
    assert mean_iou([]) == 0.0


def test_mean_iou_mixed():
    pairs = [
        ((0, 100), (0, 100)),     # 1.0
        ((0, 50), (50, 100)),     # 0.0
        ((0, 100), (50, 150)),    # 1/3
    ]
    avg = mean_iou(pairs)
    expected = (1.0 + 0.0 + 1 / 3) / 3
    assert abs(avg - expected) < 1e-9


def test_mean_iou_with_nones():
    pairs = [
        ((0, 100), (0, 100)),     # 1.0
        (None, (0, 100)),         # 0.0 ; counts as miss
    ]
    assert mean_iou(pairs) == 0.5
