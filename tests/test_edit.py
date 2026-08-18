import pytest

from core.handlers.edit import _rows_match

BASE_ENTRY = {
    "row_index": 5, "date": "01.08.2026", "type_tr": "Expense",
    "category": "Кафе", "amount": 150, "description": "обід"
}


def test_rows_match_identical_row():
    current = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    assert _rows_match(current, BASE_ENTRY) is True


def test_rows_match_false_when_row_shifted_to_different_transaction():
    # Simulates another entry being deleted above this row, shifting
    # everything up by one — this row index now holds a different
    # transaction entirely.
    current = ["02.08.2026", "Expense", "Таксі", 75, "B"]
    assert _rows_match(current, BASE_ENTRY) is False


def test_rows_match_false_when_row_deleted():
    assert _rows_match(None, BASE_ENTRY) is False


def test_rows_match_false_when_row_too_short():
    assert _rows_match(["01.08.2026", "Expense"], BASE_ENTRY) is False


def test_rows_match_tolerates_float_vs_int_amount():
    current = ["01.08.2026", "Expense", "Кафе", 150.0, "обід"]
    assert _rows_match(current, BASE_ENTRY) is True


def test_rows_match_tolerates_tiny_float_rounding():
    current = ["01.08.2026", "Expense", "Кафе", 150.0000001, "обід"]
    assert _rows_match(current, BASE_ENTRY) is True


def test_rows_match_false_on_amount_difference():
    current = ["01.08.2026", "Expense", "Кафе", 151, "обід"]
    assert _rows_match(current, BASE_ENTRY) is False


def test_rows_match_false_on_category_difference():
    current = ["01.08.2026", "Expense", "Таксі", 150, "обід"]
    assert _rows_match(current, BASE_ENTRY) is False


def test_rows_match_false_on_date_difference():
    current = ["02.08.2026", "Expense", "Кафе", 150, "обід"]
    assert _rows_match(current, BASE_ENTRY) is False


def test_rows_match_false_on_type_difference():
    # Same category/amount/description but Income instead of Expense —
    # a plausible coincidence that must still be caught.
    current = ["01.08.2026", "Income", "Кафе", 150, "обід"]
    assert _rows_match(current, BASE_ENTRY) is False