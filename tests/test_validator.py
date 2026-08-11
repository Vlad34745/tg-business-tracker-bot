import pytest

from core.validator import parse_financial_message, parse_multiline_message, dedupe_description


def test_amount_before_category_and_description():
    result = parse_financial_message("25000 Зарплата червень")
    assert result == ("Income", "Зарплата", 25000.0, "червень")


def test_amount_after_category_only():
    result = parse_financial_message("Зубний 4100")
    assert result == ("Expense", "Зубний", 4100.0, "-")


def test_category_amount_description():
    result = parse_financial_message("Таксі 220 центр")
    assert result == ("Expense", "Таксі", 220.0, "центр")


def test_amount_only():
    result = parse_financial_message("500")
    assert result == ("Expense", "Інше", 500.0, "-")


def test_decimal_comma_is_converted_to_dot():
    result = parse_financial_message("45,50 Кафе")
    assert result == ("Expense", "Кафе", 45.50, "-")


def test_decimal_dot_amount():
    result = parse_financial_message("Software 15.99 subscription")
    assert result == ("Expense", "Software", 15.99, "subscription")


def test_empty_string_returns_none():
    assert parse_financial_message("") is None


def test_whitespace_only_returns_none():
    assert parse_financial_message("   ") is None


def test_no_number_returns_none():
    assert parse_financial_message("Продукти без суми") is None


def test_income_keyword_detection():
    result = parse_financial_message("300 фріланс проект")
    assert result[0] == "Income"


def test_income_keyword_upwork_case_insensitive():
    result = parse_financial_message("12000 UPWORK оплата")
    assert result[0] == "Income"


def test_income_keyword_english_salary():
    result = parse_financial_message("25000 Salary June")
    assert result[0] == "Income"


def test_income_keyword_english_freelance():
    result = parse_financial_message("500 Freelance project")
    assert result[0] == "Income"


def test_income_keyword_ukrainian_synonyms():
    for text in ["5000 Надходження", "3000 Виплата", "15000 Стипендія", "2000 Пенсія", "1000 Гонорар", "1000 Поповнення"]:
        assert parse_financial_message(text)[0] == "Income", f"{text} should be Income"


def test_income_keyword_english_synonyms():
    for text in ["500 Earnings", "1000 Wage", "500 Stipend", "2000 Pension", "200 Refund", "1000 Grant", "300 Royalty"]:
        assert parse_financial_message(text)[0] == "Income", f"{text} should be Income"


def test_no_income_keyword_defaults_to_expense():
    result = parse_financial_message("450 Продукти АТБ")
    assert result[0] == "Expense"


def test_capitalize_does_not_lowercase_abbreviation():
    """
    Regression test: str.capitalize() used to lowercase the rest of the
    word, turning "Продукти АТБ" into "Продукти атб". Only the first
    letter should be capitalized, the rest of the category untouched.
    """
    result = parse_financial_message("Продукти АТБ 450")
    assert result == ("Expense", "Продукти АТБ", 450.0, "-")


def test_capitalize_preserves_mixed_case_brand_name():
    result = parse_financial_message("500 iPhone чохол")
    assert result[1] == "IPhone"  # first letter forced upper, rest untouched


def test_before_and_after_text_category_capitalized():
    result = parse_financial_message("поповнення рахунку 250 Київстар")
    assert result[1] == "Поповнення рахунку"
    assert result[2] == 250.0
    assert result[3] == "Київстар"


def test_parse_multiline_all_valid():
    text = "150 Обіди\n220 Таксі\n50 Кава"
    entries, failed = parse_multiline_message(text, "2026-07-26")
    assert len(entries) == 3
    assert failed == []
    assert entries[0]["category"] == "Обіди"
    assert entries[0]["amount"] == 150.0
    assert entries[1]["category"] == "Таксі"


def test_parse_multiline_skips_unparsable_lines():
    text = "150 Обіди\nце не транзакція\n50 Кава"
    entries, failed = parse_multiline_message(text, "2026-07-26")
    assert len(entries) == 2
    assert failed == ["це не транзакція"]


def test_parse_multiline_all_invalid():
    text = "привіт\nяк справи"
    entries, failed = parse_multiline_message(text, "2026-07-26")
    assert entries == []
    assert len(failed) == 2


def test_parse_multiline_ignores_blank_lines():
    text = "150 Обіди\n\n\n220 Таксі"
    entries, failed = parse_multiline_message(text, "2026-07-26")
    assert len(entries) == 2


def test_parse_multiline_uses_given_date():
    entries, _ = parse_multiline_message("150 Обіди", "2026-01-01")
    assert entries[0]["date"] == "2026-01-01"


def test_dedupe_description_removes_overlapping_prefix():
    result = dedupe_description("з шинкою та сиром купив в гроші", "Млинці з шинкою та сиром")
    assert result == "купив в гроші"


def test_dedupe_description_no_overlap_unchanged():
    result = dedupe_description("центр", "Таксі")
    assert result == "центр"


def test_dedupe_description_full_overlap_returns_dash():
    result = dedupe_description("з шинкою", "Млинці з шинкою")
    assert result == "-"


def test_dedupe_description_handles_dash():
    assert dedupe_description("-", "Кафе") == "-"


def test_dedupe_description_handles_empty():
    assert dedupe_description("", "Кафе") == ""


def test_dedupe_description_case_insensitive():
    result = dedupe_description("Шинкою та сиром купив", "млинці шинкою та сиром")
    assert result == "купив"