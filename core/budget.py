def parse_budgets_rows(rows: list) -> dict:
    """
    Convert raw "Budgets" sheet rows [category, limit] into a
    {category: limit} dict. Skips malformed rows silently.
    """
    budgets = {}
    for row in rows:
        if len(row) < 2:
            continue
        category = row[0]
        if not category:
            continue
        try:
            limit = float(str(row[1]).replace(",", "."))
        except (ValueError, TypeError):
            continue
        budgets[category] = limit
    return budgets


def check_budget_status(expense_by_category: list, budgets: dict) -> list:
    """
    Compare actual spend per category (from a report summary) against
    configured budget limits.

    Args:
        expense_by_category: [(category, spent), ...] as returned in a
            report summary's "expense_by_category" field.
        budgets: {category: monthly_limit}

    Returns:
        [(category, spent, limit), ...] only for categories that have a
        configured budget, sorted by how much over/under they are
        (most over-budget first).
    """
    results = []
    for category, spent in expense_by_category:
        if category in budgets:
            results.append((category, spent, budgets[category]))

    results.sort(key=lambda item: item[1] - item[2], reverse=True)
    return results