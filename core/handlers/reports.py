"""/report command: period + top-N category picker, chart generation."""
import re
import logging
from datetime import datetime, timedelta
from aiogram import F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.filters import Command
from core.report import (
    compute_monthly_report, format_month_label,
    compute_period_report, format_period_label, subtract_months,
    previous_period_range, previous_month, compute_change_pct
)
from core.budget import parse_budgets_rows, check_budget_status
from core.chart import generate_category_chart
from core import language
from core.i18n import t
from core.sheets import get_all_transactions, get_budgets
from core.handlers._shared import (
    router, is_owner, awaiting_report_args, awaiting_report_topn,
    PERIOD_ARGS_MAP
)

logger = logging.getLogger(__name__)

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
        rows = await get_all_transactions(user_id)
    except Exception as e:
        await answer(t("err_sheet_read", lang, e=e))
        return

    if custom_start is not None:
        summary = compute_period_report(rows, custom_start, custom_end)
        period_label = format_period_label(custom_start, custom_end)
        is_month_mode = False
        prev_summary = compute_period_report(rows, *previous_period_range(custom_start, custom_end))
    elif period_days is not None:
        end_date = now.date()
        start_date = end_date - timedelta(days=period_days - 1)
        summary = compute_period_report(rows, start_date, end_date)
        period_label = format_period_label(start_date, end_date)
        is_month_mode = False
        prev_summary = compute_period_report(rows, *previous_period_range(start_date, end_date))
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
        prev_summary = compute_monthly_report(rows, *previous_month(year, month))

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

    # Compare expenses against the immediately preceding period of the
    # same length (or the previous calendar month, in month mode).
    # Skipped when there's no prior data to compare against — a 0%
    # baseline would be misleading, not informative.
    if prev_summary["count"] > 0:
        expense_change = compute_change_pct(summary["expense_total"], prev_summary["expense_total"])
        if expense_change is not None:
            if expense_change > 0.5:
                change_icon = "🔺"
            elif expense_change < -0.5:
                change_icon = "🔻"
            else:
                change_icon = "➖"
            sign = "+" if expense_change >= 0 else ""
            lines.append(t("report_prev_period_title", lang))
            lines.append(t("report_expense_change", lang, icon=change_icon, sign=sign, pct=expense_change))

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
            budgets = parse_budgets_rows(await get_budgets(user_id))
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

    # A chart with too many bars becomes an unreadably tall image (and
    # Telegram outright rejects photos past a certain aspect ratio), so
    # past this many categories it's not worth generating at all — the
    # category list above already has the full breakdown as text.
    MAX_CHART_CATEGORIES = 20
    chart_category_count = len(summary["expense_by_category"]) if full_report else min(top_n, len(summary["expense_by_category"]))

    if chart_category_count > MAX_CHART_CATEGORIES:
        await answer(t("chart_too_large", lang, n=chart_category_count))
        return

    chart_buffer = generate_category_chart(
        summary["expense_by_category"], t("report_chart_title", lang, period_label=period_label),
        top_n=len(summary["expense_by_category"]) if full_report else top_n,
        lang=lang
    )
    if chart_buffer:
        try:
            await answer_photo(
                BufferedInputFile(chart_buffer.read(), filename="report_chart.png")
            )
        except Exception as e:
            logger.warning(f"Failed to send report chart: {e}")

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

