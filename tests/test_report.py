import pytest

from datetime import date

from core.report import (
    compute_monthly_report, format_month_label, get_frequent_categories,
    compute_period_report, format_period_label
)


SAMPLE_ROWS = [
    ["25.07.2026", "Expense", "Продукти", 450, "-"],
    ["24.07.2026", "Income", "Зарплата", 25000, "червень"],
    ["20.07.2026", "Expense", "Таксі", 220, "центр"],
    ["10.07.2026", "Expense", "Продукти", 100, "-"],
    ["01.06.2026", "Expense", "Оренда", 8000, "-"],  # different month
    ["15.07.2026", "Income", "Фріланс", 3000, "проект"],
]


def test_filters_by_month_and_year():
    report = compute_monthly_report(SAMPLE_ROWS, 2026, 7)
    assert report["count"] == 5  # excludes the June row


def test_income_and_expense_totals():
    report = compute_monthly_report(SAMPLE_ROWS, 2026, 7)
    assert report["income_total"] == 28000
    assert report["expense_total"] == 770


def test_balance_is_income_minus_expense():
    report = compute_monthly_report(SAMPLE_ROWS, 2026, 7)
    assert report["balance"] == 28000 - 770


def test_expense_by_category_grouped_and_sorted():
    report = compute_monthly_report(SAMPLE_ROWS, 2026, 7)
    categories = dict(report["expense_by_category"])
    assert categories["Продукти"] == 550  # 450 + 100 merged
    assert categories["Таксі"] == 220
    # sorted descending by total
    assert report["expense_by_category"][0][0] == "Продукти"


def test_month_with_no_matching_rows():
    report = compute_monthly_report(SAMPLE_ROWS, 2025, 1)
    assert report["count"] == 0
    assert report["income_total"] == 0
    assert report["expense_total"] == 0
    assert report["expense_by_category"] == []


def test_handles_iso_date_format_too():
    rows = [["2026-07-25", "Expense", "Кафе", 150, "-"]]
    report = compute_monthly_report(rows, 2026, 7)
    assert report["count"] == 1
    assert report["expense_total"] == 150


def test_ignores_rows_with_unparsable_date():
    rows = [["not-a-date", "Expense", "Кафе", 150, "-"]]
    report = compute_monthly_report(rows, 2026, 7)
    assert report["count"] == 0


def test_ignores_malformed_short_rows():
    rows = [["25.07.2026", "Expense"]]  # missing category/amount
    report = compute_monthly_report(rows, 2026, 7)
    assert report["count"] == 0


def test_handles_decimal_comma_amount():
    rows = [["25.07.2026", "Expense", "Кафе", "45,50", "-"]]
    report = compute_monthly_report(rows, 2026, 7)
    assert report["expense_total"] == 45.50


def test_format_month_label():
    assert format_month_label(2026, 7) == "Липень 2026"
    assert format_month_label(2026, 1) == "Січень 2026"


def test_get_frequent_categories_sorted_by_count():
    rows = [
        ["25.07.2026", "Expense", "Продукти", 100, "-"],
        ["24.07.2026", "Expense", "Продукти", 200, "-"],
        ["23.07.2026", "Expense", "Таксі", 50, "-"],
        ["22.07.2026", "Expense", "Продукти", 150, "-"],
        ["21.07.2026", "Expense", "Кафе", 80, "-"],
    ]
    result = get_frequent_categories(rows, limit=3)
    assert result[0] == "Продукти"  # appears 3 times, most frequent
    assert len(result) == 3


def test_get_frequent_categories_respects_limit():
    rows = [["25.07.2026", "Expense", f"Категорія{i}", 10, "-"] for i in range(10)]
    result = get_frequent_categories(rows, limit=4)
    assert len(result) == 4


def test_get_frequent_categories_empty_rows():
    assert get_frequent_categories([], limit=6) == []


def test_get_frequent_categories_ignores_malformed_rows():
    rows = [["25.07.2026", "Expense"]]  # missing category column
    assert get_frequent_categories(rows, limit=6) == []


PERIOD_ROWS = [
    ["26.07.2026", "Expense", "Кафе", 50, "-"],
    ["25.07.2026", "Expense", "Продукти", 200, "-"],
    ["20.07.2026", "Income", "Фріланс", 1000, "-"],
    ["10.07.2026", "Expense", "Таксі", 80, "-"],
    ["01.06.2026", "Expense", "Оренда", 8000, "-"],  # outside any test range below
]


def test_compute_period_report_filters_inclusive_range():
    report = compute_period_report(PERIOD_ROWS, date(2026, 7, 20), date(2026, 7, 26))
    assert report["count"] == 3  # excludes the 10.07 and 01.06 rows


def test_compute_period_report_single_day():
    report = compute_period_report(PERIOD_ROWS, date(2026, 7, 26), date(2026, 7, 26))
    assert report["count"] == 1
    assert report["expense_total"] == 50


def test_compute_period_report_totals():
    report = compute_period_report(PERIOD_ROWS, date(2026, 7, 1), date(2026, 7, 31))
    assert report["income_total"] == 1000
    assert report["expense_total"] == 330  # 50 + 200 + 80


def test_compute_period_report_no_matches():
    report = compute_period_report(PERIOD_ROWS, date(2025, 1, 1), date(2025, 1, 31))
    assert report["count"] == 0


def test_format_period_label_range():
    assert format_period_label(date(2026, 7, 20), date(2026, 7, 26)) == "20.07.2026 – 26.07.2026"


def test_format_period_label_single_day():
    assert format_period_label(date(2026, 7, 26), date(2026, 7, 26)) == "26.07.2026"