"""Era detection tests; make sure items appear/disappear at the right SEC milestones."""

from datetime import date

from workers.extractor.era import items_applicable, part_for_item


def test_post_2024_apple_has_item_1c():
    # Apple FY2024 10-K filed 2024-11-01, FYE 2024-09-28
    items = items_applicable(date(2024, 11, 1), date(2024, 9, 28))
    assert "1C" in items


def test_apple_2023_just_misses_item_1c():
    # Apple FY2023 10-K filed 2023-11-03, FYE 2023-09-30; BEFORE 2023-12-15 cutoff
    items = items_applicable(date(2023, 11, 3), date(2023, 9, 30))
    assert "1C" not in items


def test_pre_2005_has_no_item_1a_or_1b():
    items = items_applicable(date(2004, 3, 15), date(2003, 12, 31))
    assert "1A" not in items
    assert "1B" not in items


def test_post_2005_has_item_1a_and_1b():
    items = items_applicable(date(2006, 3, 15), date(2005, 12, 31))
    assert "1A" in items
    assert "1B" in items


def test_post_2022_has_item_9c():
    items = items_applicable(date(2022, 2, 15), date(2021, 12, 31))
    assert "9C" in items


def test_pre_2022_no_item_9c():
    items = items_applicable(date(2021, 11, 1), date(2021, 9, 30))
    assert "9C" not in items


def test_chemical_banking_1995_has_only_classic_items():
    # 1995-03-27 filing, no 1A/1B/1C/9C
    items = items_applicable(date(1995, 3, 27), date(1994, 12, 31))
    assert "1A" not in items
    assert "1B" not in items
    assert "1C" not in items
    assert "9C" not in items
    # but should have core items 1, 2, 3, 4, 5, 6, 7, 8, 10-16
    for i in ["1", "2", "3", "4", "5", "6", "7", "8", "10", "15"]:
        assert i in items


def test_part_for_item_known():
    assert part_for_item("1") == 1
    assert part_for_item("1A") == 1
    assert part_for_item("1C") == 1
    assert part_for_item("7A") == 2
    assert part_for_item("9C") == 2
    assert part_for_item("10") == 3
    assert part_for_item("14") == 3
    assert part_for_item("15") == 4
    assert part_for_item("16") == 4


def test_part_for_item_unknown_returns_zero():
    # cover-page synthetic record uses part=0
    assert part_for_item("cover") == 0
    assert part_for_item("99") == 0
