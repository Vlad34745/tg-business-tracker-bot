import calendar
from datetime import datetime, date
from typing import Optional, Callable

# Month names used in report/budget titles, e.g. "Звіт за <Month> <Year>"
# or "Report for <Month> <Year>".
MONTH_NAMES_UA = {
    1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
    5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
    9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень",
}
MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Starter categories shown as picker buttons for a brand new user who
# hasn't saved any transactions yet (see get_frequent_categories below).
# Ordered roughly by how commonly they come up for personal finance.
DEFAULT_CATEGORIES = {
    "uk": ["Продукти", "Кафе", "Транспорт", "Комуналка", "Розваги", "Одяг", "Здоров'я", "Інше"],
    "en": ["Groceries", "Cafe", "Transport", "Utilities", "Entertainment", "Clothes", "Health", "Other"],
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


def _aggregate_rows(rows: list, matches: Callable[[datetime], bool]) -> dict:
    """
    Shared aggregation core used by both compute_monthly_report and
    compute_period_report — walks the rows once, keeping only those
    whose parsed date satisfies `matches`.
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
        if not parsed_date or not matches(parsed_date):
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
    return _aggregate_rows(rows, lambda d: d.year == year and d.month == month)


def compute_period_report(rows: list, start: date, end: date) -> dict:
    """
    Aggregate raw sheet rows into a summary over an arbitrary date range
    (inclusive on both ends). Same return shape as compute_monthly_report.
    Used for "/report 7d", "/report 2 weeks", etc.
    """
    return _aggregate_rows(rows, lambda d: start <= d.date() <= end)


def previous_period_range(start: date, end: date) -> tuple[date, date]:
    """
    The immediately preceding date range of the same length (in days)
    as [start, end]. Used to compare a report period against "the
    period just before it" — e.g. this week vs. last week, or the
    last 7 days vs. the 7 days before that.
    """
    length_days = (end - start).days + 1
    prev_end = date.fromordinal(start.toordinal() - 1)
    prev_start = date.fromordinal(prev_end.toordinal() - length_days + 1)
    return prev_start, prev_end


def previous_month(year: int, month: int) -> tuple[int, int]:
    """The calendar month immediately before (year, month)."""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def compute_change_pct(current: float, previous: float) -> Optional[float]:
    """
    Percentage change from `previous` to `current`. Returns None if
    `previous` is 0 — comparing against zero prior spending isn't a
    meaningful percentage (it would be "infinite"), so the caller
    should skip showing a comparison in that case rather than divide
    by zero.
    """
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def format_month_label(year: int, month: int, lang: str = "uk") -> str:
    """e.g. 'Липень 2026' (uk) or 'July 2026' (en)"""
    names = MONTH_NAMES_EN if lang == "en" else MONTH_NAMES_UA
    return f"{names.get(month, str(month))} {year}"


def format_period_label(start: date, end: date) -> str:
    """e.g. '20.07.2026 – 26.07.2026', or just one date if start == end."""
    if start == end:
        return start.strftime("%d.%m.%Y")
    return f"{start.strftime('%d.%m.%Y')} – {end.strftime('%d.%m.%Y')}"


def subtract_months(d: date, months: int) -> date:
    """
    Subtract a number of calendar months from a date, clamping the day
    to the target month's actual length (e.g. 31.03 minus 1 month is
    28.02, not an invalid 31.02). Used for "/report 2month"-style
    periods so "2 months" means two real calendar months, not a flat
    60-day guess.
    """
    total_month_index = d.month - 1 - months
    year = d.year + total_month_index // 12
    month = total_month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def get_frequent_categories(rows: list, limit: int = 6, lang: str = "uk", use_defaults: bool = False) -> list:
    """
    Count how often each category appears across all rows and return
    the most frequent ones, most-used first. Used to power the
    "pick a category" quick-buttons when editing a pending entry.

    If `rows` has no categorized entries yet (a brand new user with
    nothing saved) and `use_defaults` is True, falls back to a small
    set of common starter categories in the given language, so the
    picker isn't empty on a person's very first interaction with the
    bot. Defaults to False since an empty result is the *correct*
    signal in some callers (e.g. /find, where suggesting category
    buttons that are guaranteed to return zero results would be worse
    than just prompting for a search term).
    """
    counts: dict = {}
    for row in rows:
        if len(row) < 3:
            continue
        category = row[2]
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1

    sorted_categories = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    if not sorted_categories and use_defaults:
        return DEFAULT_CATEGORIES.get(lang, DEFAULT_CATEGORIES["uk"])[:limit]

    return [category for category, _ in sorted_categories[:limit]]