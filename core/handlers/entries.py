"""
/last and /undo, the confirm/cancel/edit-category flow for a single
pending entry, batch confirm/cancel for multi-line input, and the
catch-all free-text handler that either parses a new transaction or
completes whichever other command's flow (report/budget/remind/find)
the user is currently mid-flow in.
"""
from datetime import datetime
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from core.validator import parse_financial_message, parse_multiline_message, dedupe_description
from core.report import get_frequent_categories
from core import reminder
from core import language
from core.i18n import t
from core.sheets import (
    append_transaction, get_last_transaction,
    delete_last_transaction, get_all_transactions,
    get_last_n_transactions, delete_last_n_transactions,
    set_budget
)
from core.handlers._shared import (
    router, is_owner, pending_entries, pending_batches,
    _store_pending_entry, _store_pending_batch, _last_action_count,
    awaiting_category_text, awaiting_report_args, awaiting_report_topn,
    PERIOD_ARGS_MAP, awaiting_budget_amount, awaiting_budget_category,
    awaiting_remind_time, awaiting_find_query, _record_recent_entry,
    _is_likely_duplicate, _build_preview_text, _build_preview_keyboard,
    _format_transaction
)
from core.handlers.reports import _generate_report
from core.handlers.find import _run_find
from core.handlers.remind import _remind_menu_keyboard

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