import pytest

from core.validator import parse_financial_message


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