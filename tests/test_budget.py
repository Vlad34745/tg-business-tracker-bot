import pytest

from core.budget import parse_budgets_rows, check_budget_status


def test_parse_budgets_rows_basic():
    rows = [["Кафе", 1000], ["Таксі", "500"]]
    result = parse_budgets_rows(rows)
    assert result == {"Кафе": 1000.0, "Таксі": 500.0}


def test_parse_budgets_rows_handles_decimal_comma():
    rows = [["Кафе", "1000,50"]]
    result = parse_budgets_rows(rows)
    assert result["Кафе"] == 1000.50


def test_parse_budgets_rows_skips_malformed():
    rows = [["Кафе"], ["Таксі", "not-a-number"], ["", 500]]
    result = parse_budgets_rows(rows)
    assert result == {}


def test_parse_budgets_rows_empty():
    assert parse_budgets_rows([]) == {}


def test_check_budget_status_flags_over_budget():
    expense_by_category = [("Кафе", 1200.0), ("Таксі", 300.0)]
    budgets = {"Кафе": 1000.0, "Таксі": 500.0}
    result = check_budget_status(expense_by_category, budgets)
    assert ("Кафе", 1200.0, 1000.0) in result
    assert ("Таксі", 300.0, 500.0) in result


def test_check_budget_status_ignores_categories_without_budget():
    expense_by_category = [("Кафе", 1200.0), ("Продукти", 500.0)]
    budgets = {"Кафе": 1000.0}
    result = check_budget_status(expense_by_category, budgets)
    assert len(result) == 1
    assert result[0][0] == "Кафе"


def test_check_budget_status_sorted_most_over_first():
    expense_by_category = [("A", 100.0), ("B", 500.0)]
    budgets = {"A": 90.0, "B": 200.0}  # A is 10 over, B is 300 over
    result = check_budget_status(expense_by_category, budgets)
    assert result[0][0] == "B"
    assert result[1][0] == "A"


def test_check_budget_status_no_budgets():
    assert check_budget_status([("Кафе", 100.0)], {}) == []