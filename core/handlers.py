import os
import re
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import (
    Message, ReplyKeyboardRemove,
    CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.filters import CommandStart, Command
from core.validator import parse_financial_message, parse_multiline_message
from core.report import (
    compute_monthly_report, format_month_label, get_frequent_categories,
    compute_period_report, format_period_label, subtract_months
)
from core.budget import parse_budgets_rows, check_budget_status
from core.chart import generate_category_chart
from core.export import build_csv
from core.search import filter_transactions
from core import reminder
from core.sheets import (
    append_transaction, get_last_transaction,
    delete_last_transaction, get_all_transactions,
    get_last_n_transactions, delete_last_n_transactions,
    get_budgets, set_budget, delete_budget
)

router = Router()

# Fetch the raw ID string from env, split it by comma and strip any whitespace
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_ID", "")
ALLOWED_IDS = [str(uid).strip() for uid in ALLOWED_IDS_RAW.split(",") if uid.strip()]

def is_owner(user_id: int) -> bool:
    """Helper function to verify if the user's ID exists within the allowed list."""
    return str(user_id) in ALLOWED_IDS

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

# Holds parsed-but-unconfirmed *batches* of transactions (multi-line
# input), keyed the same way as pending_entries but each value is a
# list of entry dicts instead of a single one.
_PENDING_BATCHES_MAX = 20
pending_batches: "OrderedDict[str, list]" = OrderedDict()


def _store_pending_batch(entries: list) -> str:
    batch_id = uuid.uuid4().hex[:8]
    pending_batches[batch_id] = entries
    while len(pending_batches) > _PENDING_BATCHES_MAX:
        pending_batches.popitem(last=False)
    return batch_id

# Remembers how many rows the most recent successful save added for each
# user (1 for a normal confirm, N for a multi-entry batch confirm), so
# /undo can remove the whole batch as one unit instead of just one row.
_last_action_count: dict = {}

# Tracks users who are mid-flow entering a custom category name for a
# pending entry: user_id -> entry_id. The next free-text message from
# that user is treated as the new category, not a new transaction.
awaiting_category_text: dict = {}

# Tracks users who tapped "✏️ Свій варіант" under the /report period
# picker: their next free-text message is parsed as report arguments
# instead of a new transaction.
awaiting_report_args: dict = {}

# Maps a period-picker button choice to the equivalent /report arguments.
PERIOD_ARGS_MAP = {
    "today": ["today"], "week": ["week"], "month": [],
    "2month": ["2month"], "year": ["year"],
}

# Tracks users who tapped "✏️ Своє число" under the category-count step:
# user_id -> the period choice they already picked. Their next free-text
# message is parsed as the top-N number to combine with that period.
awaiting_report_topn: dict = {}

# Tracks users mid-flow setting a budget: user_id -> category, once a
# category is picked (or typed), waiting for the amount as free text.
awaiting_budget_amount: dict = {}

# Tracks users who tapped "✏️ Своя категорія" under /budget's add flow:
# their next free-text message is the category name, not a transaction.
awaiting_budget_category: dict = {}

# Tracks recently *saved* transactions per user, to warn about likely
# accidental duplicates (e.g. a double-tap or a flaky connection
# resending the same message). Not a hard block — just a warning banner
# on the confirmation preview; the user can still save it if it's real.
DUPLICATE_WINDOW_SECONDS = 120
recent_entries: dict = {}


def _quick_menu_keyboard() -> InlineKeyboardMarkup:
    """
    A compact inline keyboard for the no-argument commands, attached to
    key responses so the person can tap through the bot without typing
    "/" commands manually. Unlike a persistent reply keyboard, this
    doesn't permanently occupy screen space — it's attached to a single
    message and scrolls away naturally.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Останній", callback_data="nav:last"),
            InlineKeyboardButton(text="🗑 Undo", callback_data="nav:undo"),
        ],
        [
            InlineKeyboardButton(text="📊 Звіт", callback_data="nav:report"),
            InlineKeyboardButton(text="💼 Бюджет", callback_data="nav:budget"),
        ],
        [
            InlineKeyboardButton(text="📄 Експорт", callback_data="nav:export"),
        ],
    ])


def _record_recent_entry(user_id: int, type_tr: str, category: str, amount: float) -> None:
    now = datetime.now()
    entries = recent_entries.setdefault(user_id, [])
    entries.append((now, type_tr, category, amount))
    cutoff = now - timedelta(seconds=DUPLICATE_WINDOW_SECONDS)
    recent_entries[user_id] = [e for e in entries if e[0] >= cutoff]


def _is_likely_duplicate(user_id: int, type_tr: str, category: str, amount: float) -> bool:
    cutoff = datetime.now() - timedelta(seconds=DUPLICATE_WINDOW_SECONDS)
    return any(
        ts >= cutoff and t == type_tr and c == category and a == amount
        for ts, t, c, a in recent_entries.get(user_id, [])
    )


def _build_preview_text(entry: dict) -> str:
    icon = "💰" if entry["type_tr"] == "Income" else "📉"
    warning = (
        "⚠️ <b>Схожий запис уже додано нещодавно!</b> Перевір, чи це не дубль.\n\n"
        if entry.get("is_duplicate") else ""
    )
    return (
        f"{warning}👀 <b>Перевір перед збереженням:</b>\n\n"
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
        "Перед збереженням я покажу перевірку з кнопками ✅/❌.\n"
        "Якщо схожий запис уже був нещодавно — попереджу окремо.\n\n"
        "🔎 <code>/last</code> — останній запис.\n"
        "🗑️ <code>/undo</code> — видалити останній запис (або весь пакет, якщо зберігав кілька разом).\n"
        "📊 <code>/report</code> — звіт: оберу період і кількість категорій кнопками.\n"
        "💼 <code>/budget</code> — ліміти по категоріях: перегляд/додавання/видалення кнопками.\n"
        "📄 <code>/export</code> — усі записи у CSV.\n"
        "🔍 <code>/find</code> — пошук записів (напр. <code>/find кафе</code>).\n"
        "🔔 <code>/remind</code> — щоденне нагадування о 21:00, кнопками увімкнути/вимкнути.\n\n"
        "✨ Можна надіслати кілька транзакцій одним повідомленням —\n"
        "   кожну з нового рядка, і я запропоную зберегти всі одразу.\n\n"
        "Спробуй відправити мені будь-яку транзакцію!"
    )
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await message.answer(welcome_text, reply_markup=_quick_menu_keyboard())

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
        await message.answer("📭 У таблиці ще немає жодного запису.")
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
        f"📝 <b>Опис:</b> {description}"
    )

@router.message(Command("undo"))
async def cmd_undo(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    user_id = message.from_user.id
    last_count = _last_action_count.get(user_id, 1)

    if last_count > 1:
        # The most recent save was a multi-entry batch — offer to undo
        # the whole batch as one unit instead of just the last row.
        try:
            rows = await get_last_n_transactions(last_count)
        except Exception as e:
            await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
            return

        if not rows:
            await message.answer("📭 У таблиці ще немає жодного запису для видалення.")
            return

        total = 0.0
        preview_lines = [f"⚠️ <b>Видалити останні {len(rows)} записи (збережені разом)?</b>\n"]
        for row in rows:
            date, type_tr, category, amount, description = _format_transaction(row)
            icon = "💰" if type_tr == "Income" else "📉"
            preview_lines.append(f"{icon} {category}: {amount} грн")
            try:
                total += float(str(amount).replace(",", "."))
            except (ValueError, TypeError):
                pass
        preview_lines.append(f"\n💵 <b>Разом:</b> {total:.2f} грн")

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Так, видалити {len(rows)}", callback_data=f"undo_batch_confirm:{len(rows)}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="undo_cancel"),
        ]])

        await message.answer("\n".join(preview_lines), reply_markup=confirm_keyboard)
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

@router.callback_query(F.data.startswith("undo_batch_confirm:"))
async def cb_undo_batch_confirm(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    n = int(callback.data.split(":", 1)[1])
    try:
        deleted_rows = await delete_last_n_transactions(n)
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Помилка видалення:</b> <code>{e}</code>")
        await callback.answer()
        return

    if not deleted_rows:
        await callback.message.edit_text("📭 Немає записів для видалення.")
        await callback.answer()
        return

    # Reset — the batch as a unit is now gone, so a further /undo
    # should remove just one row again unless another batch is saved.
    _last_action_count[callback.from_user.id] = 1

    await callback.message.edit_text(f"🗑️ <b>Видалено {len(deleted_rows)} записів.</b>")
    await callback.answer("Видалено")

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

async def _generate_report(user_id: int, raw_args: list, answer, answer_photo):
    """
    Shared report-building logic used by /report, the period-picker
    buttons, and the "type your own period" flow. `answer` and
    `answer_photo` are async callables (message.answer / message.answer_photo
    or their callback.message equivalents) so this works from either a
    text command or a button tap.
    """
    now = datetime.now()

    FULL_FLAG_WORDS = ("full", "all", "всі", "повний")
    full_report = any(arg.lower() in FULL_FLAG_WORDS for arg in raw_args)
    args = [arg for arg in raw_args if arg.lower() not in FULL_FLAG_WORDS]

    top_n = 5
    remaining_args = []
    for arg in args:
        top_match = re.fullmatch(r"top(\d+)", arg.lower())
        if top_match:
            top_n = int(top_match.group(1))
            if top_n < 1:
                await answer("❌ <b>Кількість категорій має бути більшою за 0.</b>")
                return
        else:
            remaining_args.append(arg)
    args = remaining_args

    period_days = None
    custom_start = None
    custom_end = None
    if args:
        first_arg = args[0].lower()
        day_match = re.fullmatch(r"(\d+)d", first_arg)
        week_match = re.fullmatch(r"(\d+)(week|weeks|тиждень|тижні|тижнів)", first_arg)
        month_match = re.fullmatch(r"(\d+)(month|months|місяць|місяці|місяців)", first_arg)

        if first_arg in ("day", "today", "сьогодні"):
            period_days = 1
        elif first_arg in ("week", "тиждень"):
            period_days = 7
        elif first_arg in ("year", "рік"):
            custom_end = now.date()
            custom_start = subtract_months(custom_end, 12)
        elif day_match:
            period_days = int(day_match.group(1))
            if period_days < 1:
                await answer("❌ <b>Кількість днів має бути більшою за 0.</b>")
                return
        elif week_match:
            period_days = int(week_match.group(1)) * 7
            if period_days < 1:
                await answer("❌ <b>Кількість тижнів має бути більшою за 0.</b>")
                return
        elif month_match:
            n_months = int(month_match.group(1))
            if n_months < 1:
                await answer("❌ <b>Кількість місяців має бути більшою за 0.</b>")
                return
            custom_end = now.date()
            custom_start = subtract_months(custom_end, n_months)

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if custom_start is not None:
        summary = compute_period_report(rows, custom_start, custom_end)
        period_label = format_period_label(custom_start, custom_end)
        is_month_mode = False
    elif period_days is not None:
        end_date = now.date()
        start_date = end_date - timedelta(days=period_days - 1)
        summary = compute_period_report(rows, start_date, end_date)
        period_label = format_period_label(start_date, end_date)
        is_month_mode = False
    else:
        year, month = now.year, now.month
        if args:
            try:
                month = int(args[0])
                if not (1 <= month <= 12):
                    raise ValueError
            except ValueError:
                await answer(
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
                    await answer("❌ <b>Невірний рік.</b>\nПриклад: <code>/report 6 2026</code>")
                    return

        summary = compute_monthly_report(rows, year, month)
        period_label = format_month_label(year, month)
        is_month_mode = True

    if summary["count"] == 0:
        await answer(f"📭 За <b>{period_label}</b> ще немає жодного запису.")
        return

    balance_icon = "📈" if summary["balance"] >= 0 else "📉"
    lines = [
        f"<b>📊 Звіт за {period_label}</b>\n",
        f"💰 <b>Дохід:</b> {summary['income_total']:.2f} грн",
        f"📉 <b>Витрати:</b> {summary['expense_total']:.2f} грн",
        f"{balance_icon} <b>Баланс:</b> {summary['balance']:.2f} грн",
    ]

    if full_report:
        categories_to_show = summary["expense_by_category"]
        section_title = "🏷️ Усі категорії витрат (за сумою):"
    else:
        categories_to_show = summary["expense_by_category"][:top_n]
        section_title = f"🏷️ Топ-{top_n} категорій витрат:"

    if categories_to_show:
        lines.append(f"\n<b>{section_title}</b>")
        for category, total in categories_to_show:
            lines.append(f"• {category}: {total:.2f} грн")

    if is_month_mode:
        try:
            budgets = parse_budgets_rows(await get_budgets())
        except Exception:
            budgets = {}
        overages = [
            (cat, spent, limit) for cat, spent, limit
            in check_budget_status(summary["expense_by_category"], budgets)
            if spent > limit
        ]
        if overages:
            lines.append("\n<b>⚠️ Перевищено ліміт:</b>")
            for category, spent, limit in overages:
                lines.append(f"🔴 {category}: {spent:.2f} / {limit:.2f} грн")

    await answer("\n".join(lines))

    chart_buffer = generate_category_chart(
        summary["expense_by_category"], f"Витрати за {period_label}",
        top_n=len(summary["expense_by_category"]) if full_report else top_n
    )
    if chart_buffer:
        await answer_photo(
            BufferedInputFile(chart_buffer.read(), filename="report_chart.png")
        )

@router.message(Command("report"))
async def cmd_report(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    raw_args = message.text.split()[1:]
    if not raw_args:
        # Plain "/report" with no arguments — ask which period instead of
        # silently defaulting to the current month.
        await message.answer("📊 <b>За який період?</b>", reply_markup=_report_period_keyboard())
        return

    await _generate_report(message.from_user.id, raw_args, message.answer, message.answer_photo)

async def _show_budget_view(user_id: int, answer):
    try:
        budgets = parse_budgets_rows(await get_budgets())
    except Exception as e:
        await answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not budgets:
        await answer(
            "📭 <b>Ліміти ще не встановлені.</b>\n\n"
            "Тисни «➕ Встановити ліміт» або пиши <code>/budget set Кафе 1000</code>"
        )
        return

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    now = datetime.now()
    summary = compute_monthly_report(rows, now.year, now.month)
    spent_by_category = dict(summary["expense_by_category"])

    lines = [f"<b>💼 Бюджет на {format_month_label(now.year, now.month)}</b>\n"]
    for category, limit in sorted(budgets.items()):
        spent = spent_by_category.get(category, 0.0)
        pct = (spent / limit * 100) if limit else 0
        icon = "🔴" if spent > limit else ("🟡" if pct >= 80 else "🟢")
        lines.append(f"{icon} <b>{category}:</b> {spent:.2f} / {limit:.2f} грн ({pct:.0f}%)")

    await answer("\n".join(lines))

def _budget_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Переглянути", callback_data="budget_view")],
        [InlineKeyboardButton(text="➕ Встановити ліміт", callback_data="budget_add")],
        [InlineKeyboardButton(text="🗑 Видалити ліміт", callback_data="budget_remove")],
    ])

@router.message(Command("budget"))
async def cmd_budget(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    args = message.text.split()[1:]

    # /budget set <category words...> <amount>
    if args and args[0].lower() == "set":
        if len(args) < 3:
            await message.answer(
                "❌ <b>Формат:</b> <code>/budget set Кафе 1000</code>"
            )
            return
        category_raw = " ".join(args[1:-1]).strip()
        category = category_raw[0].upper() + category_raw[1:] if category_raw else category_raw
        try:
            limit = float(args[-1].replace(",", "."))
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "❌ <b>Невірна сума ліміту.</b> Приклад: <code>/budget set Кафе 1000</code>"
            )
            return

        try:
            await set_budget(category, limit)
        except Exception as e:
            await message.answer(f"❌ <b>Помилка запису:</b> <code>{e}</code>")
            return

        await message.answer(
            f"✅ Ліміт для <b>{category}</b> встановлено: {limit:.2f} грн/міс"
        )
        return

    # /budget remove <category words...>
    if args and args[0].lower() in ("remove", "delete", "видалити"):
        if len(args) < 2:
            await message.answer(
                "❌ <b>Формат:</b> <code>/budget remove Кафе</code>"
            )
            return
        category = " ".join(args[1:]).strip()
        try:
            deleted = await delete_budget(category)
        except Exception as e:
            await message.answer(f"❌ <b>Помилка видалення:</b> <code>{e}</code>")
            return

        if deleted:
            await message.answer(f"🗑️ Ліміт для <b>{category}</b> видалено.")
        else:
            await message.answer(f"📭 Ліміт для <b>{category}</b> не знайдено.")
        return

    if args:
        await message.answer(
            "❌ <b>Невідома команда.</b>\n\n"
            "• <code>/budget</code> — показати всі ліміти\n"
            "• <code>/budget set Кафе 1000</code> — встановити ліміт\n"
            "• <code>/budget remove Кафе</code> — видалити ліміт"
        )
        return

    # No args: show a button menu instead of jumping straight to the view
    await message.answer("💼 <b>Бюджет — що робимо?</b>", reply_markup=_budget_menu_keyboard())

@router.callback_query(F.data == "budget_view")
async def cb_budget_view(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _show_budget_view(callback.from_user.id, callback.message.answer)

@router.callback_query(F.data == "budget_add")
async def cb_budget_add(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()

    try:
        rows = await get_all_transactions()
        top_categories = get_frequent_categories(rows, limit=6)
    except Exception:
        top_categories = []

    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"budget_set_cat:{cat}")]
        for cat in top_categories
    ]
    buttons.append([InlineKeyboardButton(text="✏️ Своя категорія", callback_data="budget_set_cat_custom")])
    await callback.message.edit_text(
        "🏷️ <b>Для якої категорії встановити ліміт?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("budget_set_cat:"))
async def cb_budget_set_category(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    category = callback.data.split(":", 1)[1]
    awaiting_budget_amount[callback.from_user.id] = category
    await callback.answer()
    await callback.message.edit_text(f"💵 Напиши суму ліміту для <b>{category}</b> (напр. <code>1000</code>):")

@router.callback_query(F.data == "budget_set_cat_custom")
async def cb_budget_set_category_custom(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    awaiting_budget_category[callback.from_user.id] = True
    await callback.answer()
    await callback.message.edit_text("✏️ Напиши назву категорії:")

@router.callback_query(F.data == "budget_remove")
async def cb_budget_remove(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()

    try:
        budgets = parse_budgets_rows(await get_budgets())
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not budgets:
        await callback.message.edit_text("📭 Ліміти ще не встановлені — нічого видаляти.")
        return

    buttons = [
        [InlineKeyboardButton(text=f"{cat} ({limit:.0f} грн)", callback_data=f"budget_del_cat:{cat}")]
        for cat in sorted(budgets.keys())
    ]
    await callback.message.edit_text(
        "🗑 <b>Який ліміт видалити?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("budget_del_cat:"))
async def cb_budget_delete_category(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    category = callback.data.split(":", 1)[1]
    try:
        deleted = await delete_budget(category)
    except Exception as e:
        await callback.message.edit_text(f"❌ <b>Помилка видалення:</b> <code>{e}</code>")
        await callback.answer()
        return

    await callback.answer("Видалено" if deleted else "Не знайдено")
    if deleted:
        await callback.message.edit_text(f"🗑️ Ліміт для <b>{category}</b> видалено.")
    else:
        await callback.message.edit_text(f"📭 Ліміт для <b>{category}</b> не знайдено.")

@router.message(Command("export"))
async def cmd_export(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not rows:
        await message.answer("📭 У таблиці ще немає жодного запису.")
        return

    csv_text = build_csv(rows)
    # utf-8-sig BOM so Excel opens Cyrillic text correctly by default
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    await message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=f"📄 Експортовано {len(rows)} записів."
    )

@router.message(Command("find"))
async def cmd_find(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "❌ <b>Формат:</b> <code>/find кафе</code>"
        )
        return

    query = parts[1].strip()
    try:
        rows = await get_all_transactions()
    except Exception as e:
        await message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    matches = filter_transactions(rows, query)
    if not matches:
        await message.answer(f"🔍 Нічого не знайдено за запитом «{query}».")
        return

    total = 0.0
    for row in matches:
        try:
            total += float(str(row[3]).replace(",", "."))
        except (ValueError, IndexError):
            pass

    MAX_SHOWN = 20
    shown = matches[-MAX_SHOWN:]
    truncated_note = f" (показано останні {MAX_SHOWN})" if len(matches) > MAX_SHOWN else ""

    lines = [f"🔍 <b>Знайдено {len(matches)} записів за «{query}»{truncated_note}:</b>\n"]
    for row in shown:
        padded = row + ["-"] * (5 - len(row))
        date, type_tr, category, amount, description = padded[:5]
        icon = "💰" if type_tr == "Income" else "📉"
        lines.append(f"{icon} {date} | {category}: {amount} грн | {description}")
    lines.append(f"\n💵 <b>Загальна сума:</b> {total:.2f} грн")

    await message.answer("\n".join(lines))

@router.message(Command("remind"))
async def cmd_remind(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    args = message.text.split()[1:]
    if not args:
        status = "🔔 увімкнені" if reminder.is_enabled() else "🔕 вимкнені"
        remind_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🔔 Увімкнути", callback_data="remind_on"),
            InlineKeyboardButton(text="🔕 Вимкнути", callback_data="remind_off"),
        ]])
        await message.answer(
            f"Нагадування зараз {status} (щодня о 21:00).",
            reply_markup=remind_keyboard
        )
        return

    sub = args[0].lower()
    if sub in ("on", "увімкнути"):
        reminder.set_enabled(True)
        await message.answer("🔔 Нагадування увімкнено (щодня о 21:00).")
    elif sub in ("off", "вимкнути"):
        reminder.set_enabled(False)
        await message.answer("🔕 Нагадування вимкнено.")
    else:
        await message.answer(
            "❌ <b>Формат:</b> <code>/remind on</code> або <code>/remind off</code>"
        )

@router.callback_query(F.data == "remind_on")
async def cb_remind_on(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    reminder.set_enabled(True)
    await callback.answer("Увімкнено")
    await callback.message.edit_text("🔔 Нагадування увімкнено (щодня о 21:00).")

@router.callback_query(F.data == "remind_off")
async def cb_remind_off(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    reminder.set_enabled(False)
    await callback.answer("Вимкнено")
    await callback.message.edit_text("🔕 Нагадування вимкнено.")

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
        transaction_data = {k: v for k, v in entry.items() if k != "is_duplicate"}
        await append_transaction(**transaction_data)
        _record_recent_entry(callback.from_user.id, entry["type_tr"], entry["category"], entry["amount"])
        _last_action_count[callback.from_user.id] = 1
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

@router.callback_query(F.data.startswith("batch_confirm:"))
async def cb_batch_confirm(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    batch_id = callback.data.split(":", 1)[1]
    entries = pending_batches.pop(batch_id, None)
    if not entries:
        await callback.answer("⌛ Записи застаріли, спробуй ще раз.", show_alert=True)
        await callback.message.edit_text("⌛ <b>Час підтвердження вичерпано.</b> Надішли транзакції ще раз.")
        return

    saved = 0
    for entry in entries:
        try:
            await append_transaction(
                date=entry["date"], type_tr=entry["type_tr"], category=entry["category"],
                amount=entry["amount"], description=entry["description"]
            )
            _record_recent_entry(callback.from_user.id, entry["type_tr"], entry["category"], entry["amount"])
            saved += 1
        except Exception:
            pass  # continue trying the rest; report the final count below

    if saved == len(entries):
        _last_action_count[callback.from_user.id] = saved
        await callback.message.edit_text(f"✅ <b>Збережено всі {saved} записів!</b>")
        await callback.answer("Збережено")
    else:
        if saved > 0:
            _last_action_count[callback.from_user.id] = saved
        await callback.message.edit_text(
            f"⚠️ <b>Збережено {saved} з {len(entries)} записів.</b> Решта не записалась через помилку."
        )
        await callback.answer()

@router.callback_query(F.data.startswith("batch_cancel:"))
async def cb_batch_cancel(callback: CallbackQuery):
    batch_id = callback.data.split(":", 1)[1]
    pending_batches.pop(batch_id, None)
    await callback.message.edit_text("❌ Скасовано — жоден запис не додано.")
    await callback.answer()

@router.callback_query(F.data == "nav:last")
async def cb_nav_last(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()

    try:
        row = await get_last_transaction()
    except Exception as e:
        await callback.message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not row:
        await callback.message.answer("📭 У таблиці ще немає жодного запису.")
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    await callback.message.answer(
        f"<b>Останній запис:</b>\n\n"
        f"📅 <b>Дата:</b> {date}\n"
        f"{icon} <b>Тип:</b> {type_tr}\n"
        f"🏷️ <b>Категорія:</b> {category}\n"
        f"💵 <b>Сума:</b> {amount} грн\n"
        f"📝 <b>Опис:</b> {description}"
    )

@router.callback_query(F.data == "nav:undo")
async def cb_nav_undo(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()

    user_id = callback.from_user.id
    last_count = _last_action_count.get(user_id, 1)

    if last_count > 1:
        try:
            rows = await get_last_n_transactions(last_count)
        except Exception as e:
            await callback.message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
            return
        if not rows:
            await callback.message.answer("📭 У таблиці ще немає жодного запису для видалення.")
            return

        total = 0.0
        preview_lines = [f"⚠️ <b>Видалити останні {len(rows)} записи (збережені разом)?</b>\n"]
        for row in rows:
            date, type_tr, category, amount, description = _format_transaction(row)
            icon = "💰" if type_tr == "Income" else "📉"
            preview_lines.append(f"{icon} {category}: {amount} грн")
            try:
                total += float(str(amount).replace(",", "."))
            except (ValueError, TypeError):
                pass
        preview_lines.append(f"\n💵 <b>Разом:</b> {total:.2f} грн")

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Так, видалити {len(rows)}", callback_data=f"undo_batch_confirm:{len(rows)}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data="undo_cancel"),
        ]])
        await callback.message.answer("\n".join(preview_lines), reply_markup=confirm_keyboard)
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await callback.message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return
    if not row:
        await callback.message.answer("📭 У таблиці ще немає жодного запису для видалення.")
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, видалити", callback_data="undo_confirm"),
        InlineKeyboardButton(text="❌ Скасувати", callback_data="undo_cancel"),
    ]])
    await callback.message.answer(
        f"⚠️ <b>Видалити цей запис?</b>\n\n"
        f"📅 <b>Дата:</b> {date}\n"
        f"{icon} <b>Тип:</b> {type_tr}\n"
        f"🏷️ <b>Категорія:</b> {category}\n"
        f"💵 <b>Сума:</b> {amount} грн\n"
        f"📝 <b>Опис:</b> {description}",
        reply_markup=confirm_keyboard
    )

def _report_period_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сьогодні", callback_data="report_period:today"),
            InlineKeyboardButton(text="Тиждень", callback_data="report_period:week"),
        ],
        [
            InlineKeyboardButton(text="Місяць", callback_data="report_period:month"),
            InlineKeyboardButton(text="2 місяці", callback_data="report_period:2month"),
        ],
        [
            InlineKeyboardButton(text="Рік", callback_data="report_period:year"),
            InlineKeyboardButton(text="✏️ Свій варіант", callback_data="report_period:custom"),
        ],
    ])

@router.callback_query(F.data == "nav:report")
async def cb_nav_report(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("📊 <b>За який період?</b>", reply_markup=_report_period_keyboard())

@router.callback_query(F.data.startswith("report_period:"))
async def cb_report_period(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    choice = callback.data.split(":", 1)[1]

    if choice == "custom":
        awaiting_report_args[callback.from_user.id] = True
        await callback.answer()
        await callback.message.edit_text(
            "✏️ Напиши період повідомленням, наприклад:\n"
            "<code>7d</code>, <code>6</code>, <code>6 2026</code>, <code>top10</code>, <code>full</code>\n"
            "або просто залиш пустим — надішли крапку <code>.</code> для поточного місяця."
        )
        return

    await callback.answer()
    top_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Топ-5", callback_data=f"report_gen:{choice}:top5"),
            InlineKeyboardButton(text="Топ-10", callback_data=f"report_gen:{choice}:top10"),
        ],
        [
            InlineKeyboardButton(text="Топ-15", callback_data=f"report_gen:{choice}:top15"),
            InlineKeyboardButton(text="Повний список", callback_data=f"report_gen:{choice}:full"),
        ],
        [
            InlineKeyboardButton(text="✏️ Своє число", callback_data=f"report_gen:{choice}:customtop"),
        ],
    ])
    await callback.message.edit_text("🏷️ <b>Скільки категорій показати?</b>", reply_markup=top_keyboard)

@router.callback_query(F.data.startswith("report_gen:"))
async def cb_report_generate(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return

    _, period_choice, top_choice = callback.data.split(":", 2)

    if top_choice == "customtop":
        awaiting_report_topn[callback.from_user.id] = period_choice
        await callback.answer()
        await callback.message.edit_text("✏️ Напиши число — скільки категорій показати (наприклад <code>7</code>):")
        return

    args = list(PERIOD_ARGS_MAP.get(period_choice, []))
    if top_choice == "full":
        args.append("full")
    elif top_choice != "top5":  # top5 is already the default, no extra arg needed
        args.append(top_choice)

    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)  # remove the picker buttons
    await _generate_report(callback.from_user.id, args, callback.message.answer, callback.message.answer_photo)

@router.callback_query(F.data == "nav:budget")
async def cb_nav_budget(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer("💼 <b>Бюджет — що робимо?</b>", reply_markup=_budget_menu_keyboard())

@router.callback_query(F.data == "nav:export")
async def cb_nav_export(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer("🔒 Доступ заблоковано.", show_alert=True)
        return
    await callback.answer()

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await callback.message.answer(f"❌ <b>Помилка читання таблиці:</b> <code>{e}</code>")
        return

    if not rows:
        await callback.message.answer("📭 У таблиці ще немає жодного запису.")
        return

    csv_text = build_csv(rows)
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await callback.message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=f"📄 Експортовано {len(rows)} записів."
    )

@router.message(F.text)
async def handle_financial_entry(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("🔒 Доступ заблоковано.")
        return

    # If we're mid-flow waiting for a custom category name, treat this
    # message as that answer instead of parsing it as a new transaction.
    user_id = message.from_user.id

    if user_id in awaiting_report_args:
        awaiting_report_args.pop(user_id)
        text = message.text.strip()
        args = [] if text == "." else text.split()
        await _generate_report(user_id, args, message.answer, message.answer_photo)
        return

    if user_id in awaiting_report_topn:
        period_choice = awaiting_report_topn.pop(user_id)
        text = message.text.strip()
        try:
            n = int(text)
            if n < 1:
                raise ValueError
        except ValueError:
            await message.answer("❌ <b>Напиши ціле число більше 0</b>, наприклад <code>7</code>.")
            return
        args = list(PERIOD_ARGS_MAP.get(period_choice, [])) + [f"top{n}"]
        await _generate_report(user_id, args, message.answer, message.answer_photo)
        return

    if user_id in awaiting_budget_category:
        awaiting_budget_category.pop(user_id)
        category_raw = message.text.strip()
        category = category_raw[0].upper() + category_raw[1:] if category_raw else category_raw
        if not category:
            await message.answer("❌ Назва категорії не може бути порожньою.")
            return
        awaiting_budget_amount[user_id] = category
        await message.answer(f"💵 Напиши суму ліміту для <b>{category}</b> (напр. <code>1000</code>):")
        return

    if user_id in awaiting_budget_amount:
        category = awaiting_budget_amount.pop(user_id)
        try:
            limit = float(message.text.strip().replace(",", "."))
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.answer("❌ <b>Напиши додатне число</b>, наприклад <code>1000</code>.")
            return
        try:
            await set_budget(category, limit)
        except Exception as e:
            await message.answer(f"❌ <b>Помилка запису:</b> <code>{e}</code>")
            return
        await message.answer(f"✅ Ліміт для <b>{category}</b> встановлено: {limit:.2f} грн/міс")
        return

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

    # Multi-line input: treat each non-empty line as a separate
    # transaction to confirm and save together.
    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    if len(lines) > 1:
        current_date = datetime.now().strftime("%Y-%m-%d")
        entries, failed_lines = parse_multiline_message(message.text, current_date)

        if not entries:
            await message.answer(
                "❌ <b>Жоден рядок не вдалося розпізнати.</b> Спробуй формат: <code>500 Продукти</code>"
            )
            return

        batch_id = _store_pending_batch(entries)

        preview_lines = [f"👀 <b>Перевір {len(entries)} записів перед збереженням:</b>\n"]
        for entry in entries:
            icon = "💰" if entry["type_tr"] == "Income" else "📉"
            preview_lines.append(
                f"{icon} {entry['category']}: {entry['amount']} грн — {entry['description']}"
            )
        if failed_lines:
            preview_lines.append(f"\n⚠️ <b>Не розпізнано ({len(failed_lines)}):</b>")
            for line in failed_lines:
                preview_lines.append(f"• {line}")

        batch_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"✅ Зберегти всі ({len(entries)})", callback_data=f"batch_confirm:{batch_id}"),
            InlineKeyboardButton(text="❌ Скасувати", callback_data=f"batch_cancel:{batch_id}"),
        ]])

        await message.answer("\n".join(preview_lines), reply_markup=batch_keyboard)
        return

    parsed_data = parse_financial_message(message.text)
    if not parsed_data:
        await message.answer("❌ <b>Не вдалося розпізнати формат.</b> Спробуй: <code>500 Продукти</code>")
        return

    type_tr, category, amount, description = parsed_data
    current_date = datetime.now().strftime("%Y-%m-%d")
    is_duplicate = _is_likely_duplicate(user_id, type_tr, category, amount)

    entry_id = _store_pending_entry({
        "date": current_date, "type_tr": type_tr, "category": category,
        "amount": amount, "description": description, "is_duplicate": is_duplicate
    })

    await message.answer(
        _build_preview_text(pending_entries[entry_id]),
        reply_markup=_build_preview_keyboard(entry_id)
    )