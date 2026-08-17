from core.search import filter_transactions, filter_transactions_indexed

SAMPLE_ROWS = [
    ["25.07.2026", "Expense", "Кафе", 150, "-"],
    ["24.07.2026", "Expense", "Таксі", 220, "центр"],
    ["23.07.2026", "Income", "Зарплата", 25000, "-"],
    ["22.07.2026", "Expense", "Продукти", 450, "кафе на розі"],
]

SAMPLE_INDEXED_ROWS = [(i + 1, row) for i, row in enumerate(SAMPLE_ROWS)]


def test_filter_matches_category_case_insensitive():
    result = filter_transactions(SAMPLE_ROWS, "кафе")
    assert len(result) == 2  # matches "Кафе" category AND "кафе на розі" description


def test_filter_matches_description():
    result = filter_transactions(SAMPLE_ROWS, "центр")
    assert len(result) == 1
    assert result[0][2] == "Таксі"


def test_filter_no_match():
    result = filter_transactions(SAMPLE_ROWS, "неіснуюча категорія")
    assert result == []


def test_filter_empty_query_returns_empty():
    assert filter_transactions(SAMPLE_ROWS, "") == []
    assert filter_transactions(SAMPLE_ROWS, "   ") == []


def test_filter_ignores_malformed_rows():
    rows = [["25.07.2026", "Expense"]]  # missing category
    assert filter_transactions(rows, "кафе") == []


def test_filter_indexed_preserves_row_index():
    result = filter_transactions_indexed(SAMPLE_INDEXED_ROWS, "центр")
    assert result == [(2, ["24.07.2026", "Expense", "Таксі", 220, "центр"])]


def test_filter_indexed_matches_category_case_insensitive():
    result = filter_transactions_indexed(SAMPLE_INDEXED_ROWS, "кафе")
    assert len(result) == 2
    assert {row_index for row_index, _row in result} == {1, 4}


def test_filter_indexed_no_match():
    assert filter_transactions_indexed(SAMPLE_INDEXED_ROWS, "неіснуюча категорія") == []


def test_filter_indexed_empty_query_returns_empty():
    assert filter_transactions_indexed(SAMPLE_INDEXED_ROWS, "") == []


def test_filter_indexed_ignores_malformed_rows():
    indexed = [(1, ["25.07.2026", "Expense"])]  # missing category
    assert filter_transactions_indexed(indexed, "кафе") == []