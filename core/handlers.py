import os
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from core.validator import parse_financial_message
from core.report import (
    compute_monthly_report, format_month_label, get_frequent_categories,
    compute_period_report, format_period_label
)
from core.sheets import (
    append_transaction, get_last_transaction,
    delete_last_transaction, get_all_transactions
)

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
        [KeyboardButton(text="/last"), KeyboardButton(text="/undo")],
        [KeyboardButton(text="/report")]
    ],
    resize_keyboard=True,
    is_persistent=True
)


def _format_transaction(row) -> tuple[str, str, str, str, str]:
    """Pad a raw sheet row to exactly 5 columns and return them."""
    padded = row + ["-"] * (5 - len(row))
    return tuple(padded[:5])

# Holds parsed-but-unconfirmed transactions, keyed by a short random id
# referenced from the inline confirm/cancel buttons' callback_data.
# Capped so a burst of unconfirmed messages can't grow this unbounded —
# this is a single-user bot, so a small cap is plenty.
_PENDING_ENTRIES_MAX = 50
pending_entries: "OrderedDict[str, dict]" = OrderedDict()


def _store_pending_entry(entry: dict) -> str:
    entry_id = uuid.uuid4().hex[:8]
    pending_entries[entry_id] = entry
    while len(pending_entries) > _PENDING_ENTRIES_MAX:
        pending_entries.popitem(last=False)  # drop oldest
    return entry_id

# Tracks users who are mid-flow entering a custom category name for a
# pending entry: user_id -> entry_id. The next free-text message from
# that user is treated as the new category, not a new transaction.
awaiting_category_text: dict = {}


def _build_preview_text(entry: dict) -> str:
    icon = "💰" if entry["type_tr"] == "Income" else "📉"
    return (
        f"👀 <b>Перевір перед збереженням:</b>\n\n"
        f"📅 <b>Дата:</b> {entry['date']}\n"
        f"{icon} <b>Тип:</b> {entry['type_tr']}\n"
        f"🏷️ <b>Категорія:</b> {entry['category']}\n"
        f"💵 <b>Сума:</b> {entry['amount']} грн\n"
        f"📝 <b>Опис:</b> {entry['description']}"
    )


def _build_preview_keyboard(entry_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Зберегти", callback_data=f"entry_confirm:{entry_id}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data=f"entry_cancel:{entry_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Змінити категорію", callback_data=f"entry_edit_cat:{entry_id}"),
        ],
    ])

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
        "Перед збереженням я покажу перевірку з кнопками ✅/❌.\n\n"
        "🔎 Команда <code>/last</code> покаже останній доданий запис.\n"
        "🗑️ Команда <code>/undo</code> видалить останній запис (з підтвердженням).\n"
        "📊 Команда <code>/report</code> покаже звіт за поточний місяць.\n"
        "   Інші варіанти: <code>/report 6</code> (червень), <code>/report 6 2026</code>,\n"
        "   <code>/report today</code>, <code>/report week</code>, <code>/report 12d</code>.\n"
        "   Додай <code>full</code> в кінець, щоб побачити всі категорії (за сумою):\n"
        "   <code>/report full</code>, <code>/report week full</code>.\n\n"
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

@router.message(Command("report"))
async def cmd_report(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    now = datetime.now()
    raw_args = message.text.split()[1:]

    # Pull out an optional "show all categories" flag, wherever it appears
    FULL_FLAG_WORDS = ("full", "all", "всі", "повний")
    full_report = any(arg.lower() in FULL_FLAG_WORDS for arg in raw_args)
    args = [arg for arg in raw_args if arg.lower() not in FULL_FLAG_WORDS]

    # Decide between "period" mode (last N days) and "month" mode.
    period_days = None
    if args:
        first_arg = args[0].lower()
        day_match = re.fullmatch(r"(\d+)d", first_arg)
        if first_arg in ("day", "today", "сьогодні"):
            period_days = 1
        elif first_arg in ("week", "тиждень"):
            period_days = 7
        elif day_match:
            period_days = int(day_match.group(1))
            if period_days < 1:
                await message.answer("❌ <b>Кількість днів має бути більшою за 0.</b>")
                return

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if period_days is not None:
        end_date = now.date()
        start_date = end_date - timedelta(days=period_days - 1)
        summary = compute_period_report(rows, start_date, end_date)
        period_label = format_period_label(start_date, end_date)
    else:
        # Month mode: /report, /report 6, or /report 6 2026
        year, month = now.year, now.month
        if args:
            try:
                month = int(args[0])
                if not (1 <= month <= 12):
                    raise ValueError
            except ValueError:
                await message.answer(
                    "❌ <b>Невірний формат.</b>\n"
                    "Приклади: <code>/report</code>, <code>/report 6</code>, "
                    "<code>/report 6 2026</code>, <code>/report 7d</code>, "
                    "<code>/report week</code>"
                )
                return

            if len(args) >= 2:
                try:
                    year = int(args[1])
                except ValueError:
                    await message.answer(
                        "❌ <b>Невірний рік.</b>\n"
                        "Приклад: <code>/report 6 2026</code>"
                    )
                    return

        summary = compute_monthly_report(rows, year, month)
        period_label = format_month_label(year, month)

    if summary["count"] == 0:
        await message.answer(
            f"📭 За <b>{period_label}</b> ще немає жодного запису.",
            reply_markup=main_keyboard
        )
        return

    balance_icon = "📈" if summary["balance"] >= 0 else "📉"

    lines = [
        f"<b>📊 Звіт за {period_label}</b>\n",
        f"💰 <b>Дохід:</b> {summary['income_total']:.2f} грн",
        f"📉 <b>Витрати:</b> {summary['expense_total']:.2f} грн",
        f"{balance_icon} <b>Баланс:</b> {summary['balance']:.2f} грн",
    ]

    if full_report:
        categories_to_show = summary["expense_by_category"]  # already sorted by amount desc
        section_title = "🏷️ Усі категорії витрат (за сумою):"
    else:
        categories_to_show = summary["expense_by_category"][:5]
        section_title = "🏷️ Топ категорій витрат:"

    if categories_to_show:
        lines.append(f"\n<b>{section_title}</b>")
        for category, total in categories_to_show:
            lines.append(f"• {category}: {total:.2f} грн")

    await message.answer("\n".join(lines), reply_markup=main_keyboard)

@router.callback_query(F.data.startswith("entry_confirm:"))
async def cb_entry_confirm(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.pop(entry_id, None)

    if not entry:
        await callback.answer("⌛ Запис застарів, спробуй надіслати ще раз.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакцію ще раз.")
        return

    try:
        await append_transaction(**entry)
        icon = "💰" if entry["type_tr"] == "Income" else "📉"
        await callback.message.edit_text(
            f"✅ <b>Запис успішно додано!</b>\n\n"
            f"📅 <b>Дата:</b> {entry['date']}\n"
            f"{icon} <b>Тип:</b> {entry['type_tr']}\n"
            f"🏷️ <b>Категорія:</b> {entry['category']}\n"
            f"💵 <b>Сума:</b> {entry['amount']} грн\n"
            f"📝 <b>Опис:</b> {entry['description']}"
        )
        await callback.answer("Збережено")
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Помилка запису:</b> <code>{e}</code>")
        await callback.answer()

@router.callback_query(F.data.startswith("entry_cancel:"))
async def cb_entry_cancel(callback: CallbackQuery):
    entry_id = callback.data.split(":", 1)[1]
    pending_entries.pop(entry_id, None)
    await callback.message.edit_text("❌ Скасовано — запис не додано.")
    await callback.answer()

@router.callback_query(F.data.startswith("entry_edit_cat:"))
async def cb_entry_edit_category(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer("⌛ Запис застарів, спробуй надіслати ще раз.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакцію ще раз.")
        return

    try:
        rows = await get_all_transactions()
        top_categories = get_frequent_categories(rows, limit=6)
    except Exception:
        top_categories = []  # fall back to just the custom-input option

    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"set_cat:{entry_id}:{cat}")]
        for cat in top_categories
    ]
    buttons.append([
        InlineKeyboardButton(text="✏️ Свій варіант", callback_data=f"custom_cat:{entry_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back_to_preview:{entry_id}")
    ])

    await callback.message.edit_text(
        "🏷️ <b>Обери категорію або введи свою:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set_cat:"))
async def cb_set_category(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    _, entry_id, new_category = callback.data.split(":", 2)
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer("⌛ Запис застарів.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакцію ще раз.")
        return

    entry["category"] = new_category
    await callback.message.edit_text(
        _build_preview_text(entry),
        reply_markup=_build_preview_keyboard(entry_id)
    )
    await callback.answer("Категорію оновлено")

@router.callback_query(F.data.startswith("custom_cat:"))
async def cb_custom_category(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    if entry_id not in pending_entries:
        await callback.answer("⌛ Запис застарів.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакцію ще раз.")
        return

    awaiting_category_text[callback.from_user.id] = entry_id
    await callback.message.edit_text("✏️ Напиши нову назву категорії повідомленням:")
    await callback.answer()

@router.callback_query(F.data.startswith("back_to_preview:"))
async def cb_back_to_preview(callback: CallbackQuery):
    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer("⌛ Запис застарів.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакцію ще раз.")
        return

    await callback.message.edit_text(
        _build_preview_text(entry),
        reply_markup=_build_preview_keyboard(entry_id)
    )
    await callback.answer()

@router.message(F.text)
async def handle_financial_entry(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    # If we're mid-flow waiting for a custom category name, treat this
    # message as that answer instead of parsing it as a new transaction.
    user_id = message.from_user.id
    if user_id in awaiting_category_text:
        entry_id = awaiting_category_text.pop(user_id)
        entry = pending_entries.get(entry_id)
        if not entry:
            await message.answer("⌛ Запис застарів, спробуй надіслати транзакцію ще раз.")
            return

        new_category = message.text.strip()
        entry["category"] = new_category[0].upper() + new_category[1:] if new_category else entry["category"]

        await message.answer(
            _build_preview_text(entry),
            reply_markup=_build_preview_keyboard(entry_id)
        )
        return

    parsed_data = parse_financial_message(message.text)
    if not parsed_data:
        await message.answer("❌ <b>Не вдалося розпізнати формат.</b> Спробуй: <code>500 Продукти</code>")
        return

    type_tr, category, amount, description = parsed_data
    current_date = datetime.now().strftime("%Y-%m-%d")

    entry_id = _store_pending_entry({
        "date": current_date, "type_tr": type_tr, "category": category,
        "amount": amount, "description": description
    })

    await message.answer(
        _build_preview_text(pending_entries[entry_id]),
        reply_markup=_build_preview_keyboard(entry_id)
    )