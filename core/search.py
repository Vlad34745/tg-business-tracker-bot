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


def filter_transactions_indexed(indexed_rows: list, query: str) -> list:
    """
    Same matching logic as filter_transactions, but for rows paired
    with their sheet row index — (row_index, row) tuples, as returned
    by core.storage.get_all_transactions_with_index. Used by /find when
    it needs to offer an edit action for each match, which requires
    knowing the exact row each result came from.
    """
    query_lower = query.strip().lower()
    if not query_lower:
        return []

    results = []
    for row_index, row in indexed_rows:
        if len(row) < 3:
            continue
        category = str(row[2]) if len(row) > 2 else ""
        description = str(row[4]) if len(row) > 4 else ""
        if query_lower in category.lower() or query_lower in description.lower():
            results.append((row_index, row))
    return results