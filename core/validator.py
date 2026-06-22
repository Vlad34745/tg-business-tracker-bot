import re
from typing import Optional, Tuple

# Income keywords tracker. If the message contains any of these, it's marked as "Income"
INCOME_KEYWORDS = {
    "дохід", "доход", "зарплата", "зп", "аванс", "інвестиції", "інвестиція", 
    "фріланс", "фоп", "fop", "кешбек", "інжур", "inzhur", "upwork", "binance", 
    "депозит", "бонус", "премія"
}

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
        category = before_text.capitalize()
        description = after_text
    elif before_text and not after_text:
        # Format: "Зубний 4100" or "Продукти АТБ 450"
        category = before_text.capitalize()
        description = "-"
    elif after_text and not before_text:
        # Format: "500 Продукти АТБ"
        words = after_text.split()
        category = words[0].capitalize()
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