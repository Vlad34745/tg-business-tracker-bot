import re
from typing import Optional, Tuple

# Income keywords tracker. If the message contains any of these, it's marked as "Income"
INCOME_KEYWORDS = {"дохід", "доход", "зарплата", "зп", "аванс", "інвестиції", "фріланс", "upwork", "фоп", "fop", "кешбек", "inzhur", "інжур"}

# Noise words to skip at the beginning of the message so they don't become categories
SKIP_WORDS = {"поповнення", "оплата", "купівля", "покупка", "рахунок", "зняття", "перевод", "переведення", "переказ", "перекази", "переказати", "переказую", "переказуємо", "переказуєш", "переказуєте", "переказують"}

def parse_financial_message(text: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Parses the user's financial text message and strips away noise words at the beginning.
    Format: <Amount> <Category> [Description] or <Category> <Amount> [Description]
    
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

    # Remove the found amount from text to parse the remaining category and description
    remaining_text = text.replace(match.group(0), '', 1).strip()
    
    # Split the remaining text into separate words
    words = remaining_text.split()
    if not words:
        return None

    # Cleaning loop: skip noise words from the beginning of the list
    while words and words[0].lower().strip(".,!?") in SKIP_WORDS:
        words.pop(0)

    # If no words left after cleaning, fallback to default values
    if not words:
        category = "Other"
        description = remaining_text
    else:
        # The first meaningful word becomes the Category, everything else goes to Description
        category = words[0].capitalize()
        description = " ".join(words[1:]) if len(words) > 1 else "-"

    # Determine transaction type based on global text search
    text_lower = text.lower()
    if any(keyword in text_lower for keyword in INCOME_KEYWORDS):
        type_tr = "Income"
    else:
        type_tr = "Expense"

    return type_tr, category, amount, description