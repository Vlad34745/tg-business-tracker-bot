import re
from typing import Optional, Tuple

# Income keywords tracker. If the message contains any of these, it's marked as "Income"
INCOME_KEYWORDS = {
    "дохід", "доход", "зарплата", "зп", "аванс", "інвестиції", "інвестиція", 
    "фріланс", "фоп", "fop", "кешбек", "інжур", "inzhur", "upwork", "binance", 
    "депозит", "бонус", "премія",
    "надходження", "виплата", "виплати", "прибуток", "заробіток",
    "стипендія", "пенсія", "гонорар", "поповнення",
    # English equivalents, for people using the bot in English
    "income", "salary", "advance", "investment", "investments",
    "freelance", "cashback", "deposit", "bonus", "prize", "payout",
    "earnings", "wage", "wages", "stipend", "pension", "refund",
    "grant", "royalty", "royalties",
}


def _capitalize_first(word: str) -> str:
    """
    Uppercase only the first character, leaving the rest of the word untouched.
    Unlike str.capitalize(), this does not lowercase the remaining characters,
    so abbreviations like "АТБ" or brand names like "iPhone" are preserved.
    """
    if not word:
        return word
    return word[0].upper() + word[1:]


def normalize_category(category: str) -> str:
    """
    Canonicalizes a category name so the same category typed different
    ways is always stored — and therefore grouped in reports and
    matched in budgets — as one and the same category. Without this,
    "Кафе", "кафе", " Кафе", and "Кафе  " would each become a distinct
    category in reports.

    Trims leading/trailing whitespace, collapses repeated internal
    whitespace to a single space, and capitalizes only the first
    letter (leaving the rest untouched — see _capitalize_first).

    This is the single place category text gets canonicalized; every
    code path that sets a category (parsed from a message, typed as a
    custom category, or typed for a budget limit) should route through
    this function before the category is stored or compared.
    """
    if not category:
        return category
    collapsed = " ".join(category.split())  # trim + collapse whitespace
    return _capitalize_first(collapsed)


def parse_financial_message(text: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Parses the user's financial text message splitting it by the position of the amount.
    Format: <Category> <Amount> <Description> or <Amount> <Category> <Description>
    
    Returns: Tuple (type_tr, category, amount, description) or None if format is invalid.
    """
    text = text.strip()
    if not text:
        return None

    # RegEx to find the transaction amount (e.g., 150, 45.50, 1000)
    match = re.search(r'\b\d+(?:[.,]\d+)?\b', text)
    if not match:
        return None

    # Extract the amount and convert it to float
    amount_str = match.group(0).replace(',', '.')
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    # Get the start and end positions of the found number inside the text
    start_pos, end_pos = match.span()

    # Split the text into what goes BEFORE the number and what goes AFTER
    before_text = text[:start_pos].strip()
    after_text = text[end_pos:].strip()

    # Determine Category and Description based on the message layout structure
    if before_text and after_text:
        # Format: "Поповнення рахунку 250 Київстар"
        category = normalize_category(before_text)
        description = after_text
    elif before_text and not after_text:
        # Format: "Зубний 4100" or "Продукти АТБ 450"
        category = normalize_category(before_text)
        description = "-"
    elif after_text and not before_text:
        # Format: "500 Продукти АТБ"
        words = after_text.split()
        category = normalize_category(words[0])
        description = " ".join(words[1:]) if len(words) > 1 else "-"
    else:
        # If only a number was sent: "500"
        category = "Інше"
        description = "-"

    # Determine transaction type based on global text search using Ukrainian/English keywords
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in INCOME_KEYWORDS):
        type_tr = "Income"
    else:
        type_tr = "Expense"

    return type_tr, category, amount, description


def parse_multiline_message(text: str, current_date: str) -> Tuple[list, list]:
    """
    Parses a message containing multiple transactions, one per line
    (e.g. a batch paste like "150 Обіди\n220 Таксі\n50 Кава").

    Returns:
        (entries, failed_lines) where entries is a list of dicts ready
        for append_transaction (date/type_tr/category/amount/description),
        and failed_lines is the raw text of any line that couldn't be
        parsed, so the caller can tell the user what was skipped.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    entries = []
    failed_lines = []

    for line in lines:
        parsed = parse_financial_message(line)
        if parsed:
            type_tr, category, amount, description = parsed
            entries.append({
                "date": current_date, "type_tr": type_tr, "category": category,
                "amount": amount, "description": description
            })
        else:
            failed_lines.append(line)

    return entries, failed_lines


def dedupe_description(description: str, category: str) -> str:
    """
    Strips leading words from `description` that are already part of
    `category` (case-insensitive, word-level). Used when a category is
    edited to a longer, custom phrase that "eats into" what was
    originally parsed as description — without this, the same words
    would appear twice (once in category, once in description).

    e.g. category "Млинці з шинкою та сиром" + leftover description
    "з шинкою та сиром купив в гроші" -> "купив в гроші"
    """
    if not description or description == "-":
        return description

    desc_words = description.split()
    category_words_lower = {w.lower() for w in category.split()}

    while desc_words and desc_words[0].lower() in category_words_lower:
        desc_words.pop(0)

    return " ".join(desc_words) if desc_words else "-"