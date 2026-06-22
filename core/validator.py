import re
from typing import Optional, Tuple

# Словник категорій доходів. Якщо повідомлення містить ці слова, це автоматично "Income"
INCOME_KEYWORDS = {"дохід", "доход", "зарплата", "зп", "аванс", "інвестиції", "фріланс", "upwork", "фоп", "fop", "кешбек", "inzhur"}

def parse_financial_message(text: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Парсить текстове повідомлення користувача.
    Формат: <Сума> <Категорія> [Опис] або <Категорія> <Сума> [Опис]
    
    Повертає: Tuple (type_tr, category, amount, description) або None, якщо формат невірний.
    """
    text = text.strip()
    if not text:
        return None

    # Регулярний вираз для пошуку числа (наприклад: 150, 45.50, 1000)
    match = re.search(r'\b\d+(?:[.,]\d+)?\b', text)
    if not match:
        return None

    # Забираємо суму та форматуємо її у float
    amount_str = match.group(0).replace(',', '.')
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    # Видаляємо знайдену суму з тексту, щоб залишилися тільки категорія та опис
    remaining_text = text.replace(match.group(0), '', 1).strip()
    
    # Розбиваємо залишок тексту на слова
    words = remaining_text.split()
    if not words:
        return None

    # Перше слово вважаємо категорією, все інше — описом (якщо є)
    category = words[0].capitalize()
    description = " ".join(words[1:]) if len(words) > 1 else "-"

    # Визначаємо тип транзакції на основі ключових слів
    category_lower = category.lower()
    if any(keyword in category_lower for keyword in INCOME_KEYWORDS) or "дохід" in text.lower():
        type_tr = "Income"
    else:
        type_tr = "Expense"

    return type_tr, category, amount, description