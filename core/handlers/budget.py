"""/budget command: view/set/remove category spending limits."""
from datetime import datetime
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from core.report import compute_monthly_report, format_month_label, get_frequent_categories
from core.budget import parse_budgets_rows
from core import language
from core.i18n import t
from core.sheets import get_all_transactions, get_budgets, set_budget, delete_budget
from core.handlers._shared import (
    router, is_owner, awaiting_budget_amount, awaiting_budget_category
)

async def _show_budget_view(user_id: int, answer):
    lang = language.get_language(user_id)
    try:
        budgets = parse_budgets_rows(await get_budgets(user_id))
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    if not budgets:
        await answer(t("budget_not_set_yet", lang))
        return

    try:
        rows = await get_all_transactions(user_id)
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
            await set_budget(message.from_user.id, category, limit)
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
            deleted = await delete_budget(message.from_user.id, category)
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
        rows = await get_all_transactions(callback.from_user.id)
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
        budgets = parse_budgets_rows(await get_budgets(callback.from_user.id))
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
        deleted = await delete_budget(callback.from_user.id, category)
    except Exception as e:
        await callback.message.edit_text(t("err_delete", lang, e=e))
        await callback.answer()
        return

    await callback.answer(t("toast_deleted", lang) if deleted else t("toast_not_found", lang))
    if deleted:
        await callback.message.edit_text(t("budget_limit_removed", lang, category=category))
    else:
        await callback.message.edit_text(t("budget_limit_not_found", lang, category=category))

@router.callback_query(F.data == "nav:budget")
async def cb_nav_budget(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(t("budget_menu_prompt", lang), reply_markup=_budget_menu_keyboard(lang))