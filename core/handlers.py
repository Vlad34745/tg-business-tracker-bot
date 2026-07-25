import os
from datetime import datetime
from aiogram import Router, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from core.validator import parse_financial_message
from core.sheets import append_transaction, get_last_transaction, delete_last_transaction

router = Router()

# Fetch the raw ID string from env, split it by comma and strip any whitespace
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_ID", "")
ALLOWED_IDS = [str(uid).strip() for uid in ALLOWED_IDS_RAW.split(",") if uid.strip()]

def is_owner(user_id: int) -> bool:
    """Helper function to verify if the user's ID exists within the allowed list."""
    return str(user_id) in ALLOWED_IDS

# Persistent keyboard shown at the bottom of the chat.
# The button sends the literal "/last" text, so it's matched by the
# Command("last") handler exactly the same way as typing it manually.
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/last"), KeyboardButton(text="/undo")]
    ],
    resize_keyboard=True,
    is_persistent=True
)


def _format_transaction(row) -> tuple[str, str, str, str, str]:
    """Pad a raw sheet row to exactly 5 columns and return them."""
    padded = row + ["-"] * (5 - len(row))
    return tuple(padded[:5])

@router.message(CommandStart())
async def cmd_start(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ обмежено. Цей бот є приватним фінансовим трекером.")
        return
        
    welcome_text = (
        "<b>Привіт! Я твій особистий Фінансовий Трекер 📊</b>\n\n"
        "Я вмію миттєво записувати твої доходи та витрати у Google Таблицю.\n\n"
        "✏️ <b>Як відправляти записи:</b>\n"
        "• <code>150 Обіди</code>\n"
        "• <code>25000 Зарплата червень</code>\n"
        "• <code>Таксі 220 центр</code>\n\n"
        "🔎 Команда <code>/last</code> покаже останній доданий запис.\n"
        "🗑️ Команда <code>/undo</code> видалить останній запис (з підтвердженням).\n\n"
        "Спробуй відправити мені будь-яку транзакцію!"
    )
    await message.answer(welcome_text, reply_markup=main_keyboard)

@router.message(Command("last"))
async def cmd_last(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not row:
        await message.answer("📭 У таблиці ще немає жодного запису.", reply_markup=main_keyboard)
        return

    # Pad the row in case some trailing columns are empty
    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"

    await message.answer(
        f"<b>Останній запис:</b>\n\n"
        f"📅 <b>Дата:</b> {date}\n"
        f"{icon} <b>Тип:</b> {type_tr}\n"
        f"🏷️ <b>Категорія:</b> {category}\n"
        f"💵 <b>Сума:</b> {amount} грн\n"
        f"📝 <b>Опис:</b> {description}",
        reply_markup=main_keyboard
    )

@router.message(Command("undo"))
async def cmd_undo(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not row:
        await message.answer("📭 У таблиці ще немає жодного запису для видалення.")
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, видалити", callback_data="undo_confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="undo_cancel"),
    ]])

    await message.answer(
        f"⚠️ <b>Видалити цей запис?</b>\n\n"
        f"📅 <b>Дата:</b> {date}\n"
        f"{icon} <b>Тип:</b> {type_tr}\n"
        f"🏷️ <b>Категорія:</b> {category}\n"
        f"💵 <b>Сума:</b> {amount} грн\n"
        f"📝 <b>Опис:</b> {description}",
        reply_markup=confirm_keyboard
    )

@router.callback_query(F.data == "undo_confirm")
async def cb_undo_confirm(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    try:
        deleted_row = await delete_last_transaction()
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Помилка видалення:</b> <code>{e}</code>")
        await callback.answer()
        return

    if not deleted_row:
        await callback.message.edit_text("📭 Немає записів для видалення.")
        await callback.answer()
        return

    await callback.message.edit_text("🗑️ <b>Запис видалено.</b>")
    await callback.answer("Видалено")

@router.callback_query(F.data == "undo_cancel")
async def cb_undo_cancel(callback: CallbackQuery):
    await callback.message.edit_text("Скасовано — запис залишився в таблиці.")
    await callback.answer()

@router.message(F.text)
async def handle_financial_entry(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    parsed_data = parse_financial_message(message.text)
    if not parsed_data:
        await message.answer("❌ <b>Не вдалося розпізнати формат.</b> Спробуй: <code>500 Продукти</code>")
        return

    type_tr, category, amount, description = parsed_data
    status_message = await message.answer("⏳ <i>Записую транзакцію в Google Таблицю...</i>")
    current_date = datetime.now().strftime("%Y-%m-%d")
    
    try:
        await append_transaction(
            date=current_date, type_tr=type_tr, category=category, amount=amount, description=description
        )
        icon = "💰" if type_tr == "Income" else "📉"
        await status_message.edit_text(
            f"✅ <b>Запис успішно додано!</b>\n\n"
            f"📅 <b>Дата:</b> {current_date}\n"
            f"{icon} <b>Тип:</b> {type_tr}\n"
            f"🏷️ <b>Категорія:</b> {category}\n"
            f"💵 <b>Сума:</b> {amount} грн\n"
            f"📝 <b>Опис:</b> {description}"
        )
    except Exception as e:
        await status_message.edit_text(f"❌ <b>Помилка запису:</b> <code>{e}</code>")