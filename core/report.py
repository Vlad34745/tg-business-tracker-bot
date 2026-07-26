from datetime import datetime
from typing import Optional

# Ukrainian month names (genitive-adjacent, used as "Звіт за <Month> <Year>")
MONTH_NAMES_UA = {
    1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
    5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
    9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень",
}

# Dates may come back from the Sheets API in either the format they were
# written in ("2026-07-25") or the sheet's display format after Google
# auto-detects the cell as a date ("25.07.2026"), depending on locale.
_DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d")


def _parse_date(date_str: str) -> Optional[datetime]:
    """Try known date formats; return None if the value can't be parsed."""
    if not date_str:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def _to_float(value) -> float:
    """Safely convert a sheet cell value (str or number) to float."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return 0.0


def compute_monthly_report(rows: list, year: int, month: int) -> dict:
    """
    Aggregate raw sheet rows into a monthly income/expense summary.

    Args:
        rows: list of [date, type, category, amount, description] rows,
              as returned by the Sheets API (extra/missing trailing
              columns are tolerated).
        year, month: the calendar month to filter and aggregate.

    Returns:
        {
            "count": int,                       # transactions matched
            "income_total": float,
            "expense_total": float,
            "balance": float,
            "expense_by_category": [(category, total), ...]  # sorted desc
        }
    """
    income_total = 0.0
    expense_total = 0.0
    expense_by_category: dict[str, float] = {}
    count = 0

    for row in rows:
        if len(row) < 4:
            continue

        date_str, type_tr, category = row[0], row[1], row[2]
        amount = _to_float(row[3])

        parsed_date = _parse_date(date_str)
        if not parsed_date:
            continue
        if parsed_date.year != year or parsed_date.month != month:
            continue

        count += 1
        if type_tr == "Income":
            income_total += amount
        else:
            expense_total += amount
            expense_by_category[category] = expense_by_category.get(category, 0.0) + amount

    sorted_categories = sorted(
        expense_by_category.items(), key=lambda item: item[1], reverse=True
    )

    return {
        "count": count,
        "income_total": income_total,
        "expense_total": expense_total,
        "balance": income_total - expense_total,
        "expense_by_category": sorted_categories,
    }


def format_month_label(year: int, month: int) -> str:
    """e.g. 'Липень 2026'"""
    return f"{MONTH_NAMES_UA.get(month, str(month))} {year}"