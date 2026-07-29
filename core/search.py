def filter_transactions(rows: list, query: str) -> list:
    """
    Return rows whose category or description contains the query
    (case-insensitive substring match). Empty query matches nothing.
    """
    query_lower = query.strip().lower()
    if not query_lower:
        return []

    results = []
    for row in rows:
        if len(row) < 3:
            continue
        category = str(row[2]) if len(row) > 2 else ""
        description = str(row[4]) if len(row) > 4 else ""
        if query_lower in category.lower() or query_lower in description.lower():
            results.append(row)
    return results