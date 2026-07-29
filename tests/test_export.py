from core.export import build_csv


def test_build_csv_has_header():
    csv_text = build_csv([])
    assert csv_text.strip() == "Date,Type,Category,Amount,Description"


def test_build_csv_includes_rows():
    rows = [["25.07.2026", "Expense", "Кафе", 150, "-"]]
    csv_text = build_csv(rows)
    lines = csv_text.strip().split("\r\n")
    assert len(lines) == 2
    assert "Кафе" in lines[1]
    assert "150" in lines[1]


def test_build_csv_pads_short_rows():
    rows = [["25.07.2026", "Expense"]]  # missing category/amount/description
    csv_text = build_csv(rows)
    lines = csv_text.strip().split("\r\n")
    assert len(lines[1].split(",")) == 5