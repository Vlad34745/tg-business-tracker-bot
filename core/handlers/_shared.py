"""
Shared router instance, in-memory state, and small helper functions used
by every handlers submodule. Kept separate from the submodules to avoid
circular imports: each submodule does `from core.handlers._shared
import router, ...` and registers its handlers on that same router
instance, which core/handlers/__init__.py then re-exports as a whole.
"""
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from aiogram import Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from core.i18n import t
from core import access

# Load .env here explicitly rather than relying on import order: this
# module reads ALLOWED_USER_ID at import time below, and it must not
# depend on some other module (e.g. core.sheets) happening to import
# — and call load_dotenv() — first. python-dotenv's load_dotenv() is
# a no-op if called more than once, so this is safe alongside the
# load_dotenv() calls in core/bot.py and core/sheets.py.
load_dotenv()

router = Router()

# Fetch the raw ID string from env, split it by comma and strip any whitespace
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_ID", "")
ALLOWED_IDS = [str(uid).strip() for uid in ALLOWED_IDS_RAW.split(",") if uid.strip()]


def is_owner(user_id: int) -> bool:
    """
    True if this user is allowed to use the bot: either their ID is in
    the static ALLOWED_USER_ID env list, or they've self-registered by
    pressing /start (see core/access.py and cmd_start in start.py).
    """
    return str(user_id) in ALLOWED_IDS or access.is_registered(user_id)


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

# Holds an in-progress /edit session for an existing (already saved)
# transaction: row_index (0-based sheet row, see core/sheets.py) plus
# the entry's current date/type/category/amount/description. Created
# when a person taps one of the recent entries listed by /edit, and
# updated in place as they change individual fields, so the sheet
# only needs one write per field actually changed rather than one
# write per keystroke.
_PENDING_EDITS_MAX = 20
pending_edits: "OrderedDict[str, dict]" = OrderedDict()


def _store_pending_edit(entry: dict) -> str:
    edit_id = uuid.uuid4().hex[:8]
    pending_edits[edit_id] = entry
    while len(pending_edits) > _PENDING_EDITS_MAX:
        pending_edits.popitem(last=False)
    return edit_id

# Remembers how many rows the most recent successful save added for each
# user (1 for a normal confirm, N for a multi-entry batch confirm), so
# /undo can remove the whole batch as one unit instead of just one row.
_last_action_count: dict = {}

# Snapshot of what /undo showed the person in its confirmation preview
# — the exact row (or rows, for a batch) it's about to delete. Checked
# again right before the actual delete in cb_undo_confirm/
# cb_undo_batch_confirm: if the sheet changed in between (e.g. a new
# entry was appended, shifting what "the last row" means), the delete
# aborts instead of silently removing the wrong data. Same defensive
# pattern as /edit's _rows_match guard, applied here to the read-then-
# delete race in delete_last_transaction/delete_last_n_transactions.
_undo_snapshot: dict = {}

# Short-lived per-user cache of the exact category list shown as
# button choices for the *current* picker (in /budget's add/remove
# flows, /find's category picker). Buttons reference a category by its
# position in this list instead of embedding the category text
# directly in callback_data — Telegram caps callback_data at 64
# *bytes*, and a longer Cyrillic category (2 bytes/char in UTF-8) can
# blow past that on its own, e.g. "Продукти для святкового столу" is
# already 60 bytes before any prefix is added. A fresh picker
# overwrites the previous entry for that user, so stale index taps
# from an old picker message naturally fail the bounds check below
# rather than resolving to some unrelated category.
_category_choices_cache: dict = {}


def _store_category_choices(user_id: int, categories: list) -> None:
    _category_choices_cache[user_id] = list(categories)


def _get_category_choice(user_id: int, index: int):
    """Returns the category at `index` from this user's most recently
    shown picker, or None if the index is out of range / nothing was
    ever stored (e.g. a stale button tap from a much older message)."""
    choices = _category_choices_cache.get(user_id, [])
    if 0 <= index < len(choices):
        return choices[index]
    return None


def _raw_rows_equal(a, b) -> bool:
    """
    True if two raw sheet rows ([date, type, category, amount,
    description]) represent the same transaction — used by /undo to
    verify the row it's about to delete still matches what it showed
    the person, tolerating float-vs-int rounding on the amount column.
    """
    if a is None or b is None:
        return False
    pa = list(a) + ["-"] * (5 - len(a))
    pb = list(b) + ["-"] * (5 - len(b))
    for i in range(5):
        if i == 3:  # amount column — compare numerically with tolerance
            try:
                if abs(float(pa[i]) - float(pb[i])) >= 0.005:
                    return False
            except (TypeError, ValueError):
                if str(pa[i]) != str(pb[i]):
                    return False
        elif str(pa[i]) != str(pb[i]):
            return False
    return True

# Tracks users who are mid-flow entering a custom category name for a
# pending entry: user_id -> entry_id. The next free-text message from
# that user is treated as the new category, not a new transaction.
awaiting_category_text: dict = {}

# Tracks users mid-flow editing a field (amount/category/description)
# of an existing saved entry via /edit: user_id -> (edit_id, field).
# The next free-text message is the new value for that field, not a
# new transaction.
awaiting_edit_field: dict = {}

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

# Every "waiting for a text reply" state above, in one place — used by
# clear_awaiting_states so starting one flow can't leave a stale state
# from an abandoned different one around to hijack a later message.
_ALL_AWAITING_DICTS = [
    awaiting_category_text, awaiting_edit_field, awaiting_report_args,
    awaiting_report_topn, awaiting_budget_amount, awaiting_budget_category,
    awaiting_remind_time, awaiting_find_query,
]


def clear_awaiting_states(user_id: int) -> bool:
    """
    Clears every "waiting for a text reply" state for this user across
    all commands (report/budget/remind/find/edit/custom-category).
    Called right before a handler sets a *new* such state, so e.g.
    tapping "Edit category" in /edit can't leave a stale
    awaiting_budget_amount from an earlier abandoned /budget flow
    around to swallow a later, unrelated message. Also used by
    /cancel, so a person can explicitly back out of whichever flow
    they're stuck in.

    Returns True if anything was actually cleared (so /cancel can
    distinguish "cancelled something" from "nothing was pending").
    """
    cleared = False
    for d in _ALL_AWAITING_DICTS:
        if d.pop(user_id, None) is not None:
            cleared = True
    return cleared

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
            InlineKeyboardButton(text=t("btn_find", lang), callback_data="nav:find"),
            InlineKeyboardButton(text=t("btn_remind", lang), callback_data="nav:remind"),
        ],
        [
            InlineKeyboardButton(text=t("btn_export", lang), callback_data="nav:export"),
            InlineKeyboardButton(text=t("btn_language", lang), callback_data="nav:language"),
        ],
        [
            InlineKeyboardButton(text=t("btn_edit", lang), callback_data="nav:edit"),
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
