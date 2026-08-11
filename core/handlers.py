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
from core.validator import parse_financial_message, parse_multiline_message, dedupe_description
from core.report import (
    compute_monthly_report, format_month_label, get_frequent_categories,
    compute_period_report, format_period_label, subtract_months
)
from core.budget import parse_budgets_rows, check_budget_status
from core.chart import generate_category_chart
from core.export import build_csv
from core.search import filter_transactions
from core import reminder
from core import language
from core.i18n import t
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

# Tracks users who tapped "➕ Додати час" under /remind: their next
# free-text message is parsed as a HH:MM reminder time, not a transaction.
awaiting_remind_time: dict = {}

# Tracks users who tapped "✏️ Ввести текст" under /find: their next
# free-text message is the search query, not a transaction.
awaiting_find_query: dict = {}

# Tracks recently *saved* transactions per user, to warn about likely
# accidental duplicates (e.g. a double-tap or a flaky connection
# resending the same message). Not a hard block — just a warning banner
# on the confirmation preview; the user can still save it if it's real.
DUPLICATE_WINDOW_SECONDS = 120
recent_entries: dict = {}


def _quick_menu_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    """
    A compact inline keyboard for the no-argument commands, attached to
    key responses so the person can tap through the bot without typing
    "/" commands manually. Unlike a persistent reply keyboard, this
    doesn't permanently occupy screen space — it's attached to a single
    message and scrolls away naturally.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_last", lang), callback_data="nav:last"),
            InlineKeyboardButton(text=t("btn_undo", lang), callback_data="nav:undo"),
        ],
        [
            InlineKeyboardButton(text=t("btn_report", lang), callback_data="nav:report"),
            InlineKeyboardButton(text=t("btn_budget", lang), callback_data="nav:budget"),
        ],
        [
            InlineKeyboardButton(text=t("btn_export", lang), callback_data="nav:export"),
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


def _build_preview_text(entry: dict, lang: str = "uk") -> str:
    icon = "💰" if entry["type_tr"] == "Income" else "📉"
    type_label = t("type_income", lang) if entry["type_tr"] == "Income" else t("type_expense", lang)
    warning = (t("duplicate_warning", lang) + "\n\n") if entry.get("is_duplicate") else ""
    return (
        f"{warning}{t('preview_check_title', lang)}\n\n"
        f"{t('label_date', lang)} {entry['date']}\n"
        f"{icon} {t('label_type', lang)} {type_label}\n"
        f"{t('label_category', lang)} {entry['category']}\n"
        f"{t('label_amount', lang)} {entry['amount']} грн\n"
        f"{t('label_description', lang)} {entry['description']}"
    )


def _build_preview_keyboard(entry_id: str, lang: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_save", lang), callback_data=f"entry_confirm:{entry_id}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=f"entry_cancel:{entry_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_edit_category", lang), callback_data=f"entry_edit_cat:{entry_id}"),
        ],
    ])

@router.message(CommandStart())
async def cmd_start(message: Message):
    lang = language.get_language(message.from_user.id)

    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied_start", lang))
        return

    if lang == "en":
        welcome_text = (
            "<b>Hi! I'm your personal Finance Tracker 📊</b>\n\n"
            "I can instantly log your income and expenses into a Google Sheet.\n\n"
            "✏️ <b>How to send entries:</b>\n"
            "• <code>150 Lunch</code>\n"
            "• <code>25000 Salary June</code>\n"
            "• <code>Taxi 220 downtown</code>\n\n"
            "Before saving, I'll show a preview with ✅/❌ buttons.\n"
            "If a similar entry was just saved, I'll flag it separately.\n\n"
            "🔎 <code>/last</code> — most recent entry.\n"
            "🗑️ <code>/undo</code> — delete the last entry (or the whole batch, if you saved several together).\n"
            "📊 <code>/report</code> — report: pick period and category count with buttons.\n"
            "💼 <code>/budget</code> — category limits: view/add/remove with buttons.\n"
            "📄 <code>/export</code> — all entries as CSV.\n"
            "🔍 <code>/find</code> — search: pick a category or enter your own text.\n"
            "🔔 <code>/remind</code> — reminders with buttons: on/off, add or remove a time.\n"
            "🌐 <code>/language</code> — switch language.\n\n"
            "✨ You can send several transactions in one message — one\n"
            "   per line — and I'll offer to save them all together.\n\n"
            "Try sending me any transaction!"
        )
    else:
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
            "🔍 <code>/find</code> — пошук: обери категорію чи «Ввести текст» кнопкою.\n"
            "🔔 <code>/remind</code> — нагадування кнопками: увімкнути/вимкнути, додати чи прибрати час.\n"
            "🌐 <code>/language</code> — зміна мови.\n\n"
            "✨ Можна надіслати кілька транзакцій одним повідомленням —\n"
            "   кожну з нового рядка, і я запропоную зберегти всі одразу.\n\n"
            "Спробуй відправити мені будь-яку транзакцію!"
        )
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await message.answer(welcome_text, reply_markup=_quick_menu_keyboard(lang))

@router.message(Command("language"))
async def cmd_language(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", language.get_language(message.from_user.id)))
        return

    lang = language.get_language(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_set:uk"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_set:en"),
    ]])
    await message.answer(t("language_prompt", lang), reply_markup=keyboard)

@router.callback_query(F.data.startswith("lang_set:"))
async def cb_set_language(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", language.get_language(callback.from_user.id)), show_alert=True)
        return

    new_lang = callback.data.split(":", 1)[1]
    language.set_language(callback.from_user.id, new_lang)
    await language.apply_commands_for_chat(callback.bot, callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(t("language_set", new_lang))

@router.message(Command("last"))
async def cmd_last(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await message.answer(t("err_sheet_read", lang, e=e))
        return

    if not row:
        await message.answer(t("no_entries_yet", lang))
        return

    # Pad the row in case some trailing columns are empty
    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    type_label = t("type_income", lang) if type_tr == "Income" else t("type_expense", lang)

    await message.answer(
        f"{t('last_entry_title', lang)}\n\n"
        f"{t('label_date', lang)} {date}\n"
        f"{icon} {t('label_type', lang)} {type_label}\n"
        f"{t('label_category', lang)} {category}\n"
        f"{t('label_amount', lang)} {amount} грн\n"
        f"{t('label_description', lang)} {description}"
    )

@router.message(Command("undo"))
async def cmd_undo(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    user_id = message.from_user.id
    last_count = _last_action_count.get(user_id, 1)

    if last_count > 1:
        # The most recent save was a multi-entry batch — offer to undo
        # the whole batch as one unit instead of just the last row.
        try:
            rows = await get_last_n_transactions(last_count)
        except Exception as e:
            await message.answer(t("err_sheet_read", lang, e=e))
            return

        if not rows:
            await message.answer(t("no_entries_to_delete", lang))
            return

        total = 0.0
        preview_lines = [t("confirm_delete_batch", lang, n=len(rows))]
        for row in rows:
            date, type_tr, category, amount, description = _format_transaction(row)
            icon = "💰" if type_tr == "Income" else "📉"
            preview_lines.append(f"{icon} {category}: {amount} грн")
            try:
                total += float(str(amount).replace(",", "."))
            except (ValueError, TypeError):
                pass
        preview_lines.append(t("total_label", lang, total=total))

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t("btn_confirm_delete_n", lang, n=len(rows)), callback_data=f"undo_batch_confirm:{len(rows)}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="undo_cancel"),
        ]])

        await message.answer("\n".join(preview_lines), reply_markup=confirm_keyboard)
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await message.answer(t("err_sheet_read", lang, e=e))
        return

    if not row:
        await message.answer(t("no_entries_to_delete", lang))
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    type_label = t("type_income", lang) if type_tr == "Income" else t("type_expense", lang)

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_confirm_delete", lang), callback_data="undo_confirm"),
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="undo_cancel"),
    ]])

    await message.answer(
        f"{t('confirm_delete_this', lang)}\n\n"
        f"{t('label_date', lang)} {date}\n"
        f"{icon} {t('label_type', lang)} {type_label}\n"
        f"{t('label_category', lang)} {category}\n"
        f"{t('label_amount', lang)} {amount} грн\n"
        f"{t('label_description', lang)} {description}",
        reply_markup=confirm_keyboard
    )

@router.callback_query(F.data.startswith("undo_batch_confirm:"))
async def cb_undo_batch_confirm(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    n = int(callback.data.split(":", 1)[1])
    try:
        deleted_rows = await delete_last_n_transactions(n)
    except Exception as e:
        await callback.message.edit_text(t("err_delete", lang, e=e))
        await callback.answer()
        return

    if not deleted_rows:
        await callback.message.edit_text(t("no_entries_to_delete_short", lang))
        await callback.answer()
        return

    # Reset — the batch as a unit is now gone, so a further /undo
    # should remove just one row again unless another batch is saved.
    _last_action_count[callback.from_user.id] = 1

    await callback.message.edit_text(t("batch_deleted", lang, n=len(deleted_rows)))
    await callback.answer(t("toast_deleted", lang))

@router.callback_query(F.data == "undo_confirm")
async def cb_undo_confirm(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    try:
        deleted_row = await delete_last_transaction()
    except Exception as e:
        await callback.message.edit_text(t("err_delete", lang, e=e))
        await callback.answer()
        return

    if not deleted_row:
        await callback.message.edit_text(t("no_entries_to_delete_short", lang))
        await callback.answer()
        return

    await callback.message.edit_text(t("entry_deleted", lang))
    await callback.answer(t("toast_deleted", lang))

@router.callback_query(F.data == "undo_cancel")
async def cb_undo_cancel(callback: CallbackQuery):
    await callback.message.edit_text(t("undo_cancelled", language.get_language(callback.from_user.id)))
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
    lang = language.get_language(user_id)

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
                await answer(t("report_categories_over_zero", lang))
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
                await answer(t("report_days_over_zero", lang))
                return
        elif week_match:
            period_days = int(week_match.group(1)) * 7
            if period_days < 1:
                await answer(t("report_weeks_over_zero", lang))
                return
        elif month_match:
            n_months = int(month_match.group(1))
            if n_months < 1:
                await answer(t("report_months_over_zero", lang))
                return
            custom_end = now.date()
            custom_start = subtract_months(custom_end, n_months)

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
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
                await answer(t("report_bad_format", lang))
                return
            if len(args) >= 2:
                try:
                    year = int(args[1])
                except ValueError:
                    await answer(t("report_bad_year", lang))
                    return

        summary = compute_monthly_report(rows, year, month)
        period_label = format_month_label(year, month, lang)
        is_month_mode = True

    if summary["count"] == 0:
        await answer(t("report_no_entries_period", lang, period_label=period_label))
        return

    balance_icon = "📈" if summary["balance"] >= 0 else "📉"
    lines = [
        t("report_title", lang, period_label=period_label),
        t("report_income_label", lang, v=summary['income_total']),
        t("report_expense_label", lang, v=summary['expense_total']),
        f"{balance_icon} " + t("report_balance_label", lang, v=summary['balance']),
    ]

    if full_report:
        categories_to_show = summary["expense_by_category"]
        section_title = t("report_all_categories_title", lang)
    else:
        categories_to_show = summary["expense_by_category"][:top_n]
        section_title = t("report_top_categories_title", lang, n=top_n)

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
            lines.append(t("report_overage_title", lang))
            for category, spent, limit in overages:
                lines.append(f"🔴 {category}: {spent:.2f} / {limit:.2f} грн")

    await answer("\n".join(lines))

    chart_buffer = generate_category_chart(
        summary["expense_by_category"], t("report_chart_title", lang, period_label=period_label),
        top_n=len(summary["expense_by_category"]) if full_report else top_n,
        lang=lang
    )
    if chart_buffer:
        await answer_photo(
            BufferedInputFile(chart_buffer.read(), filename="report_chart.png")
        )

@router.message(Command("report"))
async def cmd_report(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    raw_args = message.text.split()[1:]
    if not raw_args:
        # Plain "/report" with no arguments — ask which period instead of
        # silently defaulting to the current month.
        await message.answer(t("report_period_prompt", lang), reply_markup=_report_period_keyboard(lang))
        return

    await _generate_report(message.from_user.id, raw_args, message.answer, message.answer_photo)

async def _show_budget_view(user_id: int, answer):
    lang = language.get_language(user_id)
    try:
        budgets = parse_budgets_rows(await get_budgets())
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    if not budgets:
        await answer(t("budget_not_set_yet", lang))
        return

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    now = datetime.now()
    summary = compute_monthly_report(rows, now.year, now.month)
    spent_by_category = dict(summary["expense_by_category"])

    lines = [t("budget_month_title", lang, month_label=format_month_label(now.year, now.month, lang))]
    for category, limit in sorted(budgets.items()):
        spent = spent_by_category.get(category, 0.0)
        pct = (spent / limit * 100) if limit else 0
        icon = "🔴" if spent > limit else ("🟡" if pct >= 80 else "🟢")
        lines.append(f"{icon} <b>{category}:</b> {spent:.2f} / {limit:.2f} грн ({pct:.0f}%)")

    await answer("\n".join(lines))

def _budget_menu_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("btn_view", lang), callback_data="budget_view")],
        [InlineKeyboardButton(text=t("btn_add_limit", lang), callback_data="budget_add")],
        [InlineKeyboardButton(text=t("btn_remove_limit", lang), callback_data="budget_remove")],
    ])

@router.message(Command("budget"))
async def cmd_budget(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    args = message.text.split()[1:]

    # /budget set <category words...> <amount>
    if args and args[0].lower() == "set":
        if len(args) < 3:
            await message.answer(t("budget_format_set", lang))
            return
        category_raw = " ".join(args[1:-1]).strip()
        category = category_raw[0].upper() + category_raw[1:] if category_raw else category_raw
        try:
            limit = float(args[-1].replace(",", "."))
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.answer(t("budget_bad_amount", lang))
            return

        try:
            await set_budget(category, limit)
        except Exception as e:
            await message.answer(t("err_sheet_write", lang, e=e))
            return

        await message.answer(t("budget_limit_set", lang, category=category, limit=limit))
        return

    # /budget remove <category words...>
    if args and args[0].lower() in ("remove", "delete", "видалити"):
        if len(args) < 2:
            await message.answer(t("budget_format_remove", lang))
            return
        category = " ".join(args[1:]).strip()
        try:
            deleted = await delete_budget(category)
        except Exception as e:
            await message.answer(t("err_delete", lang, e=e))
            return

        if deleted:
            await message.answer(t("budget_limit_removed", lang, category=category))
        else:
            await message.answer(t("budget_limit_not_found", lang, category=category))
        return

    if args:
        await message.answer(t("budget_unknown_command", lang))
        return

    # No args: show a button menu instead of jumping straight to the view
    await message.answer(t("budget_menu_prompt", lang), reply_markup=_budget_menu_keyboard(lang))

@router.callback_query(F.data == "budget_view")
async def cb_budget_view(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _show_budget_view(callback.from_user.id, callback.message.answer)

@router.callback_query(F.data == "budget_add")
async def cb_budget_add(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
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
    buttons.append([InlineKeyboardButton(text=t("btn_custom_category", lang), callback_data="budget_set_cat_custom")])
    await callback.message.edit_text(
        t("budget_category_prompt", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("budget_set_cat:"))
async def cb_budget_set_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    category = callback.data.split(":", 1)[1]
    awaiting_budget_amount[callback.from_user.id] = category
    await callback.answer()
    await callback.message.edit_text(t("write_budget_amount", lang, category=category))

@router.callback_query(F.data == "budget_set_cat_custom")
async def cb_budget_set_category_custom(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    awaiting_budget_category[callback.from_user.id] = True
    await callback.answer()
    await callback.message.edit_text(t("write_category_name", lang))

@router.callback_query(F.data == "budget_remove")
async def cb_budget_remove(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    try:
        budgets = parse_budgets_rows(await get_budgets())
    except Exception as e:
        await callback.message.edit_text(t("err_sheet_read", lang, e=e))
        return

    if not budgets:
        await callback.message.edit_text(t("budget_none_to_remove", lang))
        return

    buttons = [
        [InlineKeyboardButton(text=f"{cat} ({budgets[cat]:.0f} грн)", callback_data=f"budget_del_cat:{cat}")]
        for cat in sorted(budgets.keys())
    ]
    await callback.message.edit_text(
        t("budget_which_to_remove", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )

@router.callback_query(F.data.startswith("budget_del_cat:"))
async def cb_budget_delete_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    category = callback.data.split(":", 1)[1]
    try:
        deleted = await delete_budget(category)
    except Exception as e:
        await callback.message.edit_text(t("err_delete", lang, e=e))
        await callback.answer()
        return

    await callback.answer(t("toast_deleted", lang) if deleted else t("toast_not_found", lang))
    if deleted:
        await callback.message.edit_text(t("budget_limit_removed", lang, category=category))
    else:
        await callback.message.edit_text(t("budget_limit_not_found", lang, category=category))

@router.message(Command("export"))
async def cmd_export(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await message.answer(t("err_sheet_read", lang, e=e))
        return

    if not rows:
        await message.answer(t("no_entries_yet", lang))
        return

    csv_text = build_csv(rows)
    # utf-8-sig BOM so Excel opens Cyrillic text correctly by default
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    await message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=t("export_caption", lang, n=len(rows))
    )

async def _run_find(user_id: int, query: str, answer):
    lang = language.get_language(user_id)
    try:
        rows = await get_all_transactions()
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    matches = filter_transactions(rows, query)
    if not matches:
        await answer(t("find_no_results", lang, query=query))
        return

    total = 0.0
    for row in matches:
        try:
            total += float(str(row[3]).replace(",", "."))
        except (ValueError, IndexError):
            pass

    MAX_SHOWN = 20
    shown = matches[-MAX_SHOWN:]
    truncated_note = t("find_truncated_note", lang, n=MAX_SHOWN) if len(matches) > MAX_SHOWN else ""

    lines = [t("find_results_title", lang, n=len(matches), query=query, truncated_note=truncated_note)]
    for row in shown:
        padded = row + ["-"] * (5 - len(row))
        date, type_tr, category, amount, description = padded[:5]
        icon = "💰" if type_tr == "Income" else "📉"
        lines.append(f"{icon} {date} | {category}: {amount} грн | {description}")
    lines.append(t("find_total_label", lang, total=total))

    await answer("\n".join(lines))

@router.message(Command("find"))
async def cmd_find(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        # No query given — offer buttons for the most-used categories
        # instead of just erroring out.
        try:
            rows = await get_all_transactions()
            top_categories = get_frequent_categories(rows, limit=8)
        except Exception:
            top_categories = []

        if not top_categories:
            await message.answer(t("find_format_hint", lang))
            return

        buttons = [
            [InlineKeyboardButton(text=cat, callback_data=f"find_cat:{cat}")]
            for cat in top_categories
        ]
        buttons.append([InlineKeyboardButton(text=t("btn_enter_text", lang), callback_data="find_custom")])
        await message.answer(
            t("find_prompt", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        return

    await _run_find(message.from_user.id, parts[1].strip(), message.answer)

@router.callback_query(F.data.startswith("find_cat:"))
async def cb_find_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    query = callback.data.split(":", 1)[1]
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await _run_find(callback.from_user.id, query, callback.message.answer)

@router.callback_query(F.data == "find_custom")
async def cb_find_custom(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    awaiting_find_query[callback.from_user.id] = True
    await callback.answer()
    await callback.message.edit_text(t("write_search_query", lang))

def _remind_status_text(lang: str = "uk") -> str:
    status = t("remind_status_on", lang) if reminder.is_enabled() else t("remind_status_off", lang)
    times = ", ".join(reminder.get_times())
    return t("remind_status_line", lang, status=status, times=times)

def _remind_menu_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_remind_on", lang), callback_data="remind_on"),
            InlineKeyboardButton(text=t("btn_remind_off", lang), callback_data="remind_off"),
        ],
        [
            InlineKeyboardButton(text=t("btn_add_time", lang), callback_data="remind_add_time"),
            InlineKeyboardButton(text=t("btn_remove_time", lang), callback_data="remind_remove_time"),
        ],
    ])

@router.message(Command("remind"))
async def cmd_remind(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    args = message.text.split()[1:]
    if not args:
        await message.answer(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))
        return

    sub = args[0].lower()
    if sub in ("on", "увімкнути"):
        reminder.set_enabled(True)
        await message.answer(t("remind_on_msg", lang))
    elif sub in ("off", "вимкнути"):
        reminder.set_enabled(False)
        await message.answer(t("remind_off_msg", lang))
    elif sub in ("add",) and len(args) >= 2 and reminder.is_valid_time(args[1]):
        added = reminder.add_time(args[1])
        norm = reminder.normalize_time(args[1])
        msg = t("remind_time_added", lang, time=norm) if added else t("remind_time_exists", lang, time=norm)
        await message.answer(msg)
    elif sub in ("remove", "del") and len(args) >= 2:
        removed = reminder.remove_time(reminder.normalize_time(args[1]) if reminder.is_valid_time(args[1]) else args[1])
        msg = t("remind_time_removed", lang) if removed else t("remind_time_not_found", lang)
        await message.answer(msg)
    else:
        await message.answer(t("remind_format_hint", lang))

@router.callback_query(F.data == "remind_on")
async def cb_remind_on(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    reminder.set_enabled(True)
    await callback.answer(t("toast_turned_on", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data == "remind_off")
async def cb_remind_off(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    reminder.set_enabled(False)
    await callback.answer(t("toast_turned_off", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data == "remind_add_time")
async def cb_remind_add_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    awaiting_remind_time[callback.from_user.id] = True
    await callback.answer()
    await callback.message.edit_text(t("remind_time_prompt", lang))

@router.callback_query(F.data == "remind_remove_time")
async def cb_remind_remove_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    times = reminder.get_times()
    buttons = [
        [InlineKeyboardButton(text=time_str, callback_data=f"remind_del_time:{time_str}")]
        for time_str in times
    ]
    await callback.message.edit_text(t("remind_which_to_remove", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("remind_del_time:"))
async def cb_remind_delete_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    time_str = callback.data.split(":", 1)[1]
    reminder.remove_time(time_str)
    await callback.answer(t("toast_deleted", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data.startswith("entry_confirm:"))
async def cb_entry_confirm(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.pop(entry_id, None)

    if not entry:
        await callback.answer(t("entry_expired", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body", lang))
        return

    try:
        transaction_data = {k: v for k, v in entry.items() if k != "is_duplicate"}
        await append_transaction(**transaction_data)
        _record_recent_entry(callback.from_user.id, entry["type_tr"], entry["category"], entry["amount"])
        _last_action_count[callback.from_user.id] = 1
        icon = "💰" if entry["type_tr"] == "Income" else "📉"
        type_label = t("type_income", lang) if entry["type_tr"] == "Income" else t("type_expense", lang)
        await callback.message.edit_text(
            f"{t('entry_saved_title', lang)}\n\n"
            f"{t('label_date', lang)} {entry['date']}\n"
            f"{icon} {t('label_type', lang)} {type_label}\n"
            f"{t('label_category', lang)} {entry['category']}\n"
            f"{t('label_amount', lang)} {entry['amount']} грн\n"
            f"{t('label_description', lang)} {entry['description']}"
        )
        await callback.answer(t("toast_saved", lang))
    except Exception as e:
        await callback.message.edit_text(t("err_sheet_write", lang, e=e))
        await callback.answer()

@router.callback_query(F.data.startswith("entry_cancel:"))
async def cb_entry_cancel(callback: CallbackQuery):
    entry_id = callback.data.split(":", 1)[1]
    pending_entries.pop(entry_id, None)
    await callback.message.edit_text(t("entry_cancelled", language.get_language(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data.startswith("entry_edit_cat:"))
async def cb_entry_edit_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer(t("entry_expired", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body", lang))
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
        InlineKeyboardButton(text=t("btn_custom_option", lang), callback_data=f"custom_cat:{entry_id}")
    ])
    buttons.append([
        InlineKeyboardButton(text=t("btn_back", lang), callback_data=f"back_to_preview:{entry_id}")
    ])

    await callback.message.edit_text(
        t("pick_category_prompt", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set_cat:"))
async def cb_set_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    _, entry_id, new_category = callback.data.split(":", 2)
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer(t("entry_expired_short", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body", lang))
        return

    entry["category"] = new_category
    await callback.message.edit_text(
        _build_preview_text(entry, lang),
        reply_markup=_build_preview_keyboard(entry_id, lang)
    )
    await callback.answer(t("category_updated", lang))

@router.callback_query(F.data.startswith("custom_cat:"))
async def cb_custom_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    entry_id = callback.data.split(":", 1)[1]
    if entry_id not in pending_entries:
        await callback.answer(t("entry_expired_short", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body", lang))
        return

    awaiting_category_text[callback.from_user.id] = entry_id
    await callback.message.edit_text(t("write_new_category", lang))
    await callback.answer()

@router.callback_query(F.data.startswith("back_to_preview:"))
async def cb_back_to_preview(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    entry_id = callback.data.split(":", 1)[1]
    entry = pending_entries.get(entry_id)
    if not entry:
        await callback.answer(t("entry_expired_short", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body", lang))
        return

    await callback.message.edit_text(
        _build_preview_text(entry, lang),
        reply_markup=_build_preview_keyboard(entry_id, lang)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("batch_confirm:"))
async def cb_batch_confirm(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    batch_id = callback.data.split(":", 1)[1]
    entries = pending_batches.pop(batch_id, None)
    if not entries:
        await callback.answer(t("entries_expired", lang), show_alert=True)
        await callback.message.edit_text(t("confirmation_expired_body_batch", lang))
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
        await callback.message.edit_text(t("batch_saved_all", lang, n=saved))
        await callback.answer(t("toast_saved", lang))
    else:
        if saved > 0:
            _last_action_count[callback.from_user.id] = saved
        await callback.message.edit_text(
            t("batch_saved_partial", lang, saved=saved, total=len(entries))
        )
        await callback.answer()

@router.callback_query(F.data.startswith("batch_cancel:"))
async def cb_batch_cancel(callback: CallbackQuery):
    batch_id = callback.data.split(":", 1)[1]
    pending_batches.pop(batch_id, None)
    await callback.message.edit_text(t("batch_cancelled", language.get_language(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data == "nav:last")
async def cb_nav_last(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    try:
        row = await get_last_transaction()
    except Exception as e:
        await callback.message.answer(t("err_sheet_read", lang, e=e))
        return

    if not row:
        await callback.message.answer(t("no_entries_yet", lang))
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    type_label = t("type_income", lang) if type_tr == "Income" else t("type_expense", lang)
    await callback.message.answer(
        f"{t('last_entry_title', lang)}\n\n"
        f"{t('label_date', lang)} {date}\n"
        f"{icon} {t('label_type', lang)} {type_label}\n"
        f"{t('label_category', lang)} {category}\n"
        f"{t('label_amount', lang)} {amount} грн\n"
        f"{t('label_description', lang)} {description}"
    )

@router.callback_query(F.data == "nav:undo")
async def cb_nav_undo(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    user_id = callback.from_user.id
    last_count = _last_action_count.get(user_id, 1)

    if last_count > 1:
        try:
            rows = await get_last_n_transactions(last_count)
        except Exception as e:
            await callback.message.answer(t("err_sheet_read", lang, e=e))
            return
        if not rows:
            await callback.message.answer(t("no_entries_to_delete", lang))
            return

        total = 0.0
        preview_lines = [t("confirm_delete_batch", lang, n=len(rows))]
        for row in rows:
            date, type_tr, category, amount, description = _format_transaction(row)
            icon = "💰" if type_tr == "Income" else "📉"
            preview_lines.append(f"{icon} {category}: {amount} грн")
            try:
                total += float(str(amount).replace(",", "."))
            except (ValueError, TypeError):
                pass
        preview_lines.append(t("total_label", lang, total=total))

        confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t("btn_confirm_delete_n", lang, n=len(rows)), callback_data=f"undo_batch_confirm:{len(rows)}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="undo_cancel"),
        ]])
        await callback.message.answer("\n".join(preview_lines), reply_markup=confirm_keyboard)
        return

    try:
        row = await get_last_transaction()
    except Exception as e:
        await callback.message.answer(t("err_sheet_read", lang, e=e))
        return
    if not row:
        await callback.message.answer(t("no_entries_to_delete", lang))
        return

    date, type_tr, category, amount, description = _format_transaction(row)
    icon = "💰" if type_tr == "Income" else "📉"
    type_label = t("type_income", lang) if type_tr == "Income" else t("type_expense", lang)
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_confirm_delete", lang), callback_data="undo_confirm"),
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data="undo_cancel"),
    ]])
    await callback.message.answer(
        f"{t('confirm_delete_this', lang)}\n\n"
        f"{t('label_date', lang)} {date}\n"
        f"{icon} {t('label_type', lang)} {type_label}\n"
        f"{t('label_category', lang)} {category}\n"
        f"{t('label_amount', lang)} {amount} грн\n"
        f"{t('label_description', lang)} {description}",
        reply_markup=confirm_keyboard
    )

def _report_period_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_today", lang), callback_data="report_period:today"),
            InlineKeyboardButton(text=t("btn_week", lang), callback_data="report_period:week"),
        ],
        [
            InlineKeyboardButton(text=t("btn_month", lang), callback_data="report_period:month"),
            InlineKeyboardButton(text=t("btn_2months", lang), callback_data="report_period:2month"),
        ],
        [
            InlineKeyboardButton(text=t("btn_year", lang), callback_data="report_period:year"),
            InlineKeyboardButton(text=t("btn_custom_option", lang), callback_data="report_period:custom"),
        ],
    ])

@router.callback_query(F.data == "nav:report")
async def cb_nav_report(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(t("report_period_prompt", lang), reply_markup=_report_period_keyboard(lang))

@router.callback_query(F.data.startswith("report_period:"))
async def cb_report_period(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    choice = callback.data.split(":", 1)[1]

    if choice == "custom":
        awaiting_report_args[callback.from_user.id] = True
        await callback.answer()
        await callback.message.edit_text(t("report_custom_period_prompt", lang))
        return

    await callback.answer()
    top_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_top5", lang), callback_data=f"report_gen:{choice}:top5"),
            InlineKeyboardButton(text=t("btn_top10", lang), callback_data=f"report_gen:{choice}:top10"),
        ],
        [
            InlineKeyboardButton(text=t("btn_top15", lang), callback_data=f"report_gen:{choice}:top15"),
            InlineKeyboardButton(text=t("btn_full_list", lang), callback_data=f"report_gen:{choice}:full"),
        ],
        [
            InlineKeyboardButton(text=t("btn_custom_number", lang), callback_data=f"report_gen:{choice}:customtop"),
        ],
    ])
    await callback.message.edit_text(t("report_topn_prompt", lang), reply_markup=top_keyboard)

@router.callback_query(F.data.startswith("report_gen:"))
async def cb_report_generate(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    _, period_choice, top_choice = callback.data.split(":", 2)

    if top_choice == "customtop":
        awaiting_report_topn[callback.from_user.id] = period_choice
        await callback.answer()
        await callback.message.edit_text(t("report_custom_topn_prompt", lang))
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
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(t("budget_menu_prompt", lang), reply_markup=_budget_menu_keyboard(lang))

@router.callback_query(F.data == "nav:export")
async def cb_nav_export(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    try:
        rows = await get_all_transactions()
    except Exception as e:
        await callback.message.answer(t("err_sheet_read", lang, e=e))
        return

    if not rows:
        await callback.message.answer(t("no_entries_yet", lang))
        return

    csv_text = build_csv(rows)
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await callback.message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=t("export_caption", lang, n=len(rows))
    )

@router.message(F.text)
async def handle_financial_entry(message: Message):
    user_id = message.from_user.id
    lang = language.get_language(user_id)
    if not is_owner(user_id):
        await message.answer(t("access_denied", lang))
        return

    # If we're mid-flow waiting for a custom category name, treat this
    # message as that answer instead of parsing it as a new transaction.

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
            await message.answer(t("int_over_zero_prompt", lang))
            return
        args = list(PERIOD_ARGS_MAP.get(period_choice, [])) + [f"top{n}"]
        await _generate_report(user_id, args, message.answer, message.answer_photo)
        return

    if user_id in awaiting_remind_time:
        awaiting_remind_time.pop(user_id)
        text = message.text.strip()
        if not reminder.is_valid_time(text):
            await message.answer(t("remind_time_invalid", lang))
            return
        added = reminder.add_time(text)
        norm = reminder.normalize_time(text)
        msg = t("remind_time_added", lang, time=norm) if added else t("remind_time_exists", lang, time=norm)
        await message.answer(msg, reply_markup=_remind_menu_keyboard(lang))
        return

    if user_id in awaiting_find_query:
        awaiting_find_query.pop(user_id)
        query = message.text.strip()
        if not query:
            await message.answer(t("query_empty", lang))
            return
        await _run_find(user_id, query, message.answer)
        return

    if user_id in awaiting_budget_category:
        awaiting_budget_category.pop(user_id)
        category_raw = message.text.strip()
        category = category_raw[0].upper() + category_raw[1:] if category_raw else category_raw
        if not category:
            await message.answer(t("category_empty", lang))
            return
        awaiting_budget_amount[user_id] = category
        await message.answer(t("write_budget_amount", lang, category=category))
        return

    if user_id in awaiting_budget_amount:
        category = awaiting_budget_amount.pop(user_id)
        try:
            limit = float(message.text.strip().replace(",", "."))
            if limit <= 0:
                raise ValueError
        except ValueError:
            await message.answer(t("positive_number_prompt", lang))
            return
        try:
            await set_budget(category, limit)
        except Exception as e:
            await message.answer(t("err_sheet_write", lang, e=e))
            return
        await message.answer(t("budget_limit_set", lang, category=category, limit=limit))
        return

    if user_id in awaiting_category_text:
        entry_id = awaiting_category_text.pop(user_id)
        entry = pending_entries.get(entry_id)
        if not entry:
            await message.answer(t("entry_expired", lang))
            return

        new_category = message.text.strip()
        if new_category:
            entry["description"] = dedupe_description(entry["description"], new_category)
            entry["category"] = new_category[0].upper() + new_category[1:]

        await message.answer(
            _build_preview_text(entry, lang),
            reply_markup=_build_preview_keyboard(entry_id, lang)
        )
        return

    # Multi-line input: treat each non-empty line as a separate
    # transaction to confirm and save together.
    lines = [line.strip() for line in message.text.split("\n") if line.strip()]
    if len(lines) > 1:
        current_date = datetime.now().strftime("%Y-%m-%d")
        entries, failed_lines = parse_multiline_message(message.text, current_date)

        if not entries:
            await message.answer(t("unrecognized_lines", lang))
            return

        batch_id = _store_pending_batch(entries)

        preview_lines = [t("batch_preview_title", lang, n=len(entries))]
        for entry in entries:
            icon = "💰" if entry["type_tr"] == "Income" else "📉"
            preview_lines.append(
                f"{icon} {entry['category']}: {entry['amount']} грн — {entry['description']}"
            )
        if failed_lines:
            preview_lines.append(t("batch_unrecognized_title", lang, n=len(failed_lines)))
            for line in failed_lines:
                preview_lines.append(f"• {line}")

        batch_keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t("btn_save_all", lang, n=len(entries)), callback_data=f"batch_confirm:{batch_id}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=f"batch_cancel:{batch_id}"),
        ]])

        await message.answer("\n".join(preview_lines), reply_markup=batch_keyboard)
        return

    parsed_data = parse_financial_message(message.text)
    if not parsed_data:
        await message.answer(t("unrecognized_format", lang))
        return

    type_tr, category, amount, description = parsed_data
    current_date = datetime.now().strftime("%Y-%m-%d")
    is_duplicate = _is_likely_duplicate(user_id, type_tr, category, amount)

    entry_id = _store_pending_entry({
        "date": current_date, "type_tr": type_tr, "category": category,
        "amount": amount, "description": description, "is_duplicate": is_duplicate
    })

    await message.answer(
        _build_preview_text(pending_entries[entry_id], lang),
        reply_markup=_build_preview_keyboard(entry_id, lang)
    )