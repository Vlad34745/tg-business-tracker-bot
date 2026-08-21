"""
/edit command: unlike /last and /undo (which only ever touch the most
recent entry), this lets a person pick any of their recent entries and
change its amount, category, or description, or delete it outright.
"""
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from core.i18n import t
from core import language
from core.sheets import get_recent_transactions_with_index, get_transaction_row, delete_transaction_row
from core.handlers._shared import (
    router, is_owner, pending_edits, _store_pending_edit, awaiting_edit_field,
    clear_awaiting_states
)


def _entry_button_label(row: list) -> str:
    padded = row + ["-"] * (5 - len(row))
    date, type_tr, category, amount, _description = padded[:5]
    icon = "💰" if type_tr == "Income" else "📉"
    return f"{icon} {date} {category}: {amount}"


def _rows_match(current_row, entry: dict) -> bool:
    """
    True if `current_row` (freshly fetched from the sheet) still holds
    the same data as `entry` (what /edit last showed the person).
    Used to guard update_transaction_row/delete_transaction_row calls:
    if another entry was deleted in between and rows shifted, the row
    at `entry["row_index"]` may now belong to a *different*
    transaction — writing to it without this check would silently
    corrupt the wrong row.
    """
    if current_row is None or len(current_row) < 3:
        return False
    padded = list(current_row) + ["-"] * (5 - len(current_row))
    date, type_tr, category, amount, description = padded[:5]
    try:
        amount_matches = abs(float(amount) - float(entry["amount"])) < 0.005
    except (TypeError, ValueError):
        amount_matches = str(amount) == str(entry["amount"])
    return (
        str(date) == str(entry["date"])
        and str(type_tr) == str(entry["type_tr"])
        and str(category) == str(entry["category"])
        and amount_matches
        and str(description) == str(entry["description"])
    )


def _build_edit_detail_text(entry: dict, lang: str) -> str:
    icon = "💰" if entry["type_tr"] == "Income" else "📉"
    type_label = t("type_income", lang) if entry["type_tr"] == "Income" else t("type_expense", lang)
    return t(
        "edit_entry_detail", lang,
        label_date=t("label_date", lang), date=entry["date"],
        icon=icon, label_type=t("label_type", lang), type_label=type_label,
        label_category=t("label_category", lang), category=entry["category"],
        label_amount=t("label_amount", lang), amount=entry["amount"],
        label_description=t("label_description", lang), description=entry["description"]
    )


def _build_edit_detail_keyboard(edit_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_edit_amount", lang), callback_data=f"edit_amount:{edit_id}"),
            InlineKeyboardButton(text=t("btn_edit_field_category", lang), callback_data=f"edit_category:{edit_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_edit_description", lang), callback_data=f"edit_desc:{edit_id}"),
        ],
        [
            InlineKeyboardButton(text=t("btn_delete", lang), callback_data=f"edit_delete:{edit_id}"),
            InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=f"edit_cancel:{edit_id}"),
        ],
    ])


async def _show_edit_picker(user_id: int, answer, lang: str, offset: int = 0):
    try:
        page, has_more = await get_recent_transactions_with_index(user_id, n=10, offset=offset)
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    if not page and offset == 0:
        await answer(t("edit_no_entries", lang))
        return
    if not page:
        # Paged past the oldest entry — nothing further back to show,
        # but still offer a way back to the more recent pages rather
        # than dead-ending here.
        back_offset = max(0, offset - 10)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t("btn_show_newer", lang), callback_data=f"edit_page:{back_offset}")
        ]])
        await answer(t("edit_no_older_entries", lang), reply_markup=keyboard)
        return

    # Most recent first for easier scanning.
    buttons = []
    for row_index, row in reversed(page):
        padded = row + ["-"] * (5 - len(row))
        date, type_tr, category, amount, description = padded[:5]
        edit_id = _store_pending_edit({
            "row_index": row_index, "date": date, "type_tr": type_tr,
            "category": category, "amount": amount, "description": description
        })
        buttons.append([InlineKeyboardButton(
            text=_entry_button_label(row), callback_data=f"edit_pick:{edit_id}"
        )])

    # "Newer" and "Older" nav buttons share a row when both are
    # available, so paging back and forth doesn't grow the keyboard.
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(
            text=t("btn_show_newer", lang), callback_data=f"edit_page:{max(0, offset - 10)}"
        ))
    if has_more:
        nav_row.append(InlineKeyboardButton(
            text=t("btn_show_older", lang), callback_data=f"edit_page:{offset + 10}"
        ))
    if nav_row:
        buttons.append(nav_row)

    await answer(t("edit_pick_prompt", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(Command("edit"))
async def cmd_edit(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return
    await _show_edit_picker(message.from_user.id, message.answer, lang)


@router.callback_query(F.data == "nav:edit")
async def cb_nav_edit(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await _show_edit_picker(callback.from_user.id, callback.message.answer, lang)


@router.callback_query(F.data.startswith("edit_page:"))
async def cb_edit_page(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    try:
        offset = int(callback.data.split(":", 1)[1])
    except ValueError:
        offset = 0

    await callback.answer()

    async def edit_in_place(text, reply_markup=None):
        await callback.message.edit_text(text, reply_markup=reply_markup)

    await _show_edit_picker(callback.from_user.id, edit_in_place, lang, offset=offset)


@router.callback_query(F.data.startswith("edit_pick:"))
async def cb_edit_pick(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    entry = pending_edits.get(edit_id)
    if not entry:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    await callback.answer()
    await callback.message.edit_text(
        _build_edit_detail_text(entry, lang),
        reply_markup=_build_edit_detail_keyboard(edit_id, lang)
    )


@router.callback_query(F.data.startswith("edit_amount:"))
async def cb_edit_amount(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    if edit_id not in pending_edits:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    clear_awaiting_states(callback.from_user.id)
    awaiting_edit_field[callback.from_user.id] = (edit_id, "amount")
    await callback.answer()
    await callback.message.edit_text(t("edit_prompt_amount", lang))


@router.callback_query(F.data.startswith("edit_category:"))
async def cb_edit_category(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    if edit_id not in pending_edits:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    clear_awaiting_states(callback.from_user.id)
    awaiting_edit_field[callback.from_user.id] = (edit_id, "category")
    await callback.answer()
    await callback.message.edit_text(t("edit_prompt_category", lang))


@router.callback_query(F.data.startswith("edit_desc:"))
async def cb_edit_desc(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    if edit_id not in pending_edits:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    clear_awaiting_states(callback.from_user.id)
    awaiting_edit_field[callback.from_user.id] = (edit_id, "description")
    await callback.answer()
    await callback.message.edit_text(t("edit_prompt_description", lang))


@router.callback_query(F.data.startswith("edit_delete:"))
async def cb_edit_delete(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    entry = pending_edits.get(edit_id)
    if not entry:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("btn_delete", lang), callback_data=f"edit_delete_confirm:{edit_id}"),
        InlineKeyboardButton(text=t("btn_cancel", lang), callback_data=f"edit_pick:{edit_id}"),
    ]])
    await callback.message.edit_text(t("edit_delete_confirm", lang), reply_markup=keyboard)


@router.callback_query(F.data.startswith("edit_delete_confirm:"))
async def cb_edit_delete_confirm(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return

    edit_id = callback.data.split(":", 1)[1]
    entry = pending_edits.pop(edit_id, None)
    if not entry:
        await callback.answer(t("edit_expired", lang), show_alert=True)
        return

    try:
        current_row = await get_transaction_row(callback.from_user.id, entry["row_index"])
    except Exception as e:
        await callback.message.edit_text(t("err_sheet_read", lang, e=e))
        await callback.answer()
        return

    if not _rows_match(current_row, entry):
        await callback.message.edit_text(t("edit_row_changed", lang))
        await callback.answer()
        return

    try:
        deleted = await delete_transaction_row(callback.from_user.id, entry["row_index"])
    except Exception as e:
        await callback.message.edit_text(t("err_delete", lang, e=e))
        await callback.answer()
        return

    if deleted:
        await callback.message.edit_text(t("edit_deleted", lang))
    else:
        await callback.message.edit_text(t("edit_delete_failed", lang))
    await callback.answer()


@router.callback_query(F.data.startswith("edit_cancel:"))
async def cb_edit_cancel(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    edit_id = callback.data.split(":", 1)[1]
    pending_edits.pop(edit_id, None)
    await callback.answer()
    await callback.message.edit_text(t("edit_cancelled", lang))