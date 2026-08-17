"""/find command: search transactions by category or free-text query."""
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from core.report import get_frequent_categories
from core.search import filter_transactions_indexed
from core import language
from core.i18n import t
from core.sheets import get_all_transactions, get_all_transactions_with_index
from core.handlers._shared import router, is_owner, awaiting_find_query, _store_pending_edit

# How many of the most recent matches get an "✏️" edit button attached.
# Matches beyond this are still shown in the summary text but without
# a button, to keep the keyboard from growing unreasonably long on a
# broad search.
MAX_EDIT_BUTTONS = 10


async def _run_find(user_id: int, query: str, answer):
    lang = language.get_language(user_id)
    try:
        indexed_rows = await get_all_transactions_with_index(user_id)
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    matches = filter_transactions_indexed(indexed_rows, query)
    if not matches:
        await answer(t("find_no_results", lang, query=query))
        return

    total = 0.0
    for _row_index, row in matches:
        try:
            total += float(str(row[3]).replace(",", "."))
        except (ValueError, IndexError):
            pass

    MAX_SHOWN = 20
    shown = matches[-MAX_SHOWN:]
    truncated_note = t("find_truncated_note", lang, n=MAX_SHOWN) if len(matches) > MAX_SHOWN else ""

    lines = [t("find_results_title", lang, n=len(matches), query=query, truncated_note=truncated_note)]
    # Most recent first, both in the text and for numbering the edit buttons.
    shown_recent_first = list(reversed(shown))
    for i, (_row_index, row) in enumerate(shown_recent_first, start=1):
        padded = row + ["-"] * (5 - len(row))
        date, type_tr, category, amount, description = padded[:5]
        icon = "💰" if type_tr == "Income" else "📉"
        marker = f"{i}. " if i <= MAX_EDIT_BUTTONS else ""
        lines.append(f"{marker}{icon} {date} | {category}: {amount} грн | {description}")
    lines.append(t("find_total_label", lang, total=total))

    buttons = []
    for i, (row_index, row) in enumerate(shown_recent_first[:MAX_EDIT_BUTTONS], start=1):
        padded = row + ["-"] * (5 - len(row))
        date, type_tr, category, amount, description = padded[:5]
        edit_id = _store_pending_edit({
            "row_index": row_index, "date": date, "type_tr": type_tr,
            "category": category, "amount": amount, "description": description
        })
        buttons.append(InlineKeyboardButton(text=f"✏️ {i}", callback_data=f"edit_pick:{edit_id}"))

    # Pack the numbered edit buttons 5 per row so they don't run off-screen.
    keyboard = None
    if buttons:
        rows_of_buttons = [buttons[j:j + 5] for j in range(0, len(buttons), 5)]
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows_of_buttons)

    await answer("\n".join(lines), reply_markup=keyboard)

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
            rows = await get_all_transactions(message.from_user.id)
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

@router.callback_query(F.data == "nav:find")
async def cb_nav_find(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    try:
        rows = await get_all_transactions(callback.from_user.id)
        top_categories = get_frequent_categories(rows, limit=8)
    except Exception:
        top_categories = []

    if not top_categories:
        await callback.message.answer(t("find_format_hint", lang))
        return

    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"find_cat:{cat}")]
        for cat in top_categories
    ]
    buttons.append([InlineKeyboardButton(text=t("btn_enter_text", lang), callback_data="find_custom")])
    await callback.message.answer(
        t("find_prompt", lang),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )