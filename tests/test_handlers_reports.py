import pytest
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock

from core.handlers import reports as h
from core.handlers import _shared
from core.i18n import t
from core.report import format_month_label, previous_month
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data


def _row(on: date, category: str, amount, type_tr: str = "Expense", description: str = "-"):
    return [on.strftime("%d.%m.%Y"), type_tr, category, amount, description]


TODAY = datetime.now().date()


def _previous_month_day() -> date:
    year, month = previous_month(TODAY.year, TODAY.month)
    return date(year, month, 15)


@pytest.fixture
def report_env(monkeypatch):
    """Storage mocked out; returns the mocks plus a recorder for what the
    report sent back (text messages and photos)."""
    env = type("Env", (), {})()
    env.get_all_transactions = AsyncMock(return_value=[])
    env.get_budgets = AsyncMock(return_value=[])
    monkeypatch.setattr(h, "get_all_transactions", env.get_all_transactions)
    monkeypatch.setattr(h, "get_budgets", env.get_budgets)
    env.answer = AsyncMock()
    env.answer_photo = AsyncMock()
    yield env
    _shared.awaiting_report_args.clear()
    _shared.awaiting_report_topn.clear()


async def _run(env, args):
    await h._generate_report(OWNER_ID, args, env.answer, env.answer_photo)


def _text(env, call=0) -> str:
    return env.answer.await_args_list[call].args[0]


# --- argument validation ---

@pytest.mark.asyncio
@pytest.mark.parametrize("args,key", [
    (["top0"], "report_categories_over_zero"),
    (["0d"], "report_days_over_zero"),
    (["0week"], "report_weeks_over_zero"),
    (["0month"], "report_months_over_zero"),
])
async def test_invalid_period_arguments_are_rejected_before_reading_the_sheet(report_env, args, key):
    await _run(report_env, args)
    report_env.answer.assert_awaited_once_with(t(key, "uk"))
    report_env.get_all_transactions.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("args,key", [
    (["13"], "report_bad_format"),
    (["0"], "report_bad_format"),
    (["abc"], "report_bad_format"),
    (["6", "abcd"], "report_bad_year"),
])
async def test_invalid_month_or_year_is_rejected(report_env, args, key):
    await _run(report_env, args)
    report_env.answer.assert_awaited_once_with(t(key, "uk"))
    report_env.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_reports_sheet_read_error(report_env):
    report_env.get_all_transactions.side_effect = RuntimeError("boom")
    await _run(report_env, [])
    assert "boom" in _text(report_env)


# --- period selection ---

@pytest.mark.asyncio
async def test_no_entries_in_period(report_env):
    await _run(report_env, [])
    label = format_month_label(TODAY.year, TODAY.month, "uk")
    report_env.answer.assert_awaited_once_with(t("report_no_entries_period", "uk", period_label=label))
    report_env.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_is_current_month_with_summary_and_chart(report_env):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, "Кафе", 300), _row(TODAY, "Таксі", 100), _row(TODAY, "Зарплата", 1000, "Income"),
    ]

    await _run(report_env, [])

    text = _text(report_env)
    assert "Кафе: 300.00" in text and "Таксі: 100.00" in text
    assert t("report_income_label", "uk", v=1000.0) in text
    assert t("report_expense_label", "uk", v=400.0) in text
    photo = report_env.answer_photo.await_args.args[0]
    assert photo.filename == "report_chart.png"


@pytest.mark.asyncio
@pytest.mark.parametrize("args,days_ago", [
    (["today"], 0), (["сьогодні"], 0), (["week"], 3), (["3d"], 2), (["2week"], 10),
])
async def test_rolling_periods_include_recent_rows(report_env, args, days_ago):
    report_env.get_all_transactions.return_value = [_row(TODAY - timedelta(days=days_ago), "Кафе", 50)]
    await _run(report_env, args)
    assert "Кафе: 50.00" in _text(report_env)


@pytest.mark.asyncio
async def test_rolling_period_excludes_older_rows(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY - timedelta(days=30), "Кафе", 50)]
    await _run(report_env, ["today"])
    assert "Кафе: 50.00" not in _text(report_env)


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [["2month"], ["year"], ["1month"]])
async def test_calendar_periods_include_recent_rows(report_env, args):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 70)]
    await _run(report_env, args)
    assert "Кафе: 70.00" in _text(report_env)


@pytest.mark.asyncio
async def test_explicit_month_and_year(report_env):
    report_env.get_all_transactions.return_value = [
        _row(date(2025, 6, 10), "Кафе", 111), _row(TODAY, "Таксі", 222),
    ]
    await _run(report_env, ["6", "2025"])
    text = _text(report_env)
    assert "Кафе: 111.00" in text and "Таксі" not in text


# --- category limiting ---

@pytest.mark.asyncio
async def test_top_n_limits_listed_categories(report_env):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, f"Кат{i}", 100 - i) for i in range(1, 6)
    ]
    await _run(report_env, ["top2"])
    text = _text(report_env)
    assert "Кат1" in text and "Кат2" in text and "Кат3" not in text


@pytest.mark.asyncio
async def test_full_flag_lists_every_category(report_env):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, f"Кат{i}", 100 - i) for i in range(1, 8)
    ]
    await _run(report_env, ["full"])
    text = _text(report_env)
    assert t("report_all_categories_title", "uk") in text
    assert "Кат7" in text


@pytest.mark.asyncio
async def test_too_many_categories_skips_the_chart(report_env):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, f"Кат{i}", 100 + i) for i in range(25)
    ]
    await _run(report_env, ["full"])
    assert report_env.answer.await_args_list[1].args[0] == t("chart_too_large", "uk", n=25)
    report_env.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_income_only_period_sends_no_chart(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Зарплата", 1000, "Income")]
    await _run(report_env, [])
    report_env.answer_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_chart_send_failure_does_not_break_the_report(report_env, caplog):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 300)]
    report_env.answer_photo.side_effect = RuntimeError("telegram said no")
    with caplog.at_level("WARNING"):
        await _run(report_env, [])
    assert "Failed to send report chart" in caplog.text


# --- comparison with the previous period ---

@pytest.mark.asyncio
@pytest.mark.parametrize("current,previous,icon,sign", [
    (200, 100, "🔺", "+"),
    (50, 100, "🔻", ""),
    (100, 100, "➖", "+"),
])
async def test_month_over_month_expense_change(report_env, current, previous, icon, sign):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, "Кафе", current), _row(_previous_month_day(), "Кафе", previous),
    ]
    await _run(report_env, [])
    text = _text(report_env)
    assert t("report_prev_period_title", "uk") in text
    assert icon in text


@pytest.mark.asyncio
async def test_rolling_period_is_compared_with_the_period_before_it(report_env):
    report_env.get_all_transactions.return_value = [
        _row(TODAY, "Кафе", 200), _row(TODAY - timedelta(days=1), "Кафе", 100),
    ]
    await _run(report_env, ["today"])
    assert "🔺" in _text(report_env)


@pytest.mark.asyncio
async def test_no_comparison_without_previous_data(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 200)]
    await _run(report_env, [])
    assert t("report_prev_period_title", "uk") not in _text(report_env)


# --- budget warnings (month mode only) ---

@pytest.mark.asyncio
async def test_over_budget_categories_are_flagged(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 300), _row(TODAY, "Таксі", 10)]
    report_env.get_budgets.return_value = [["Кафе", 100], ["Таксі", 1000]]
    await _run(report_env, [])
    text = _text(report_env)
    assert t("report_overage_title", "uk") in text
    assert "🔴 Кафе: 300.00 / 100.00" in text
    assert "🔴 Таксі" not in text


@pytest.mark.asyncio
async def test_budget_read_failure_is_ignored(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 300)]
    report_env.get_budgets.side_effect = RuntimeError("boom")
    await _run(report_env, [])
    assert "Кафе: 300.00" in _text(report_env)


@pytest.mark.asyncio
async def test_budget_warnings_are_skipped_for_rolling_periods(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 300)]
    report_env.get_budgets.return_value = [["Кафе", 100]]
    await _run(report_env, ["today"])
    report_env.get_budgets.assert_not_awaited()


# --- /report command ---

@pytest.mark.asyncio
async def test_report_command_denied_for_stranger(report_env):
    message = make_message(STRANGER_ID, "/report")
    await h.cmd_report(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


@pytest.mark.asyncio
async def test_report_command_without_args_asks_for_period(report_env):
    message = make_message(OWNER_ID, "/report")
    await h.cmd_report(message)
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert callbacks == ["report_period:today", "report_period:week", "report_period:month",
                         "report_period:2month", "report_period:year", "report_period:custom"]


@pytest.mark.asyncio
async def test_report_command_with_args_generates_report(report_env):
    report_env.get_all_transactions.return_value = [_row(TODAY, "Кафе", 300)]
    message = make_message(OWNER_ID, "/report today")
    await h.cmd_report(message)
    assert "Кафе: 300.00" in message.answer.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_nav_report_asks_for_period(report_env):
    callback = make_callback(OWNER_ID, "nav:report")
    await h.cb_nav_report(callback)
    assert callback.message.answer.await_args.args[0] == t("report_period_prompt", "uk")


# --- period / top-N pickers ---

@pytest.mark.asyncio
async def test_custom_period_button_waits_for_text(report_env):
    callback = make_callback(OWNER_ID, "report_period:custom")
    await h.cb_report_period(callback)
    assert _shared.awaiting_report_args[OWNER_ID] is True
    callback.message.edit_text.assert_awaited_once_with(t("report_custom_period_prompt", "uk"))


@pytest.mark.asyncio
async def test_period_button_shows_top_n_picker(report_env):
    callback = make_callback(OWNER_ID, "report_period:week")
    await h.cb_report_period(callback)
    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert callbacks == ["report_gen:week:top5", "report_gen:week:top10", "report_gen:week:top15",
                         "report_gen:week:full", "report_gen:week:customtop"]


@pytest.mark.asyncio
async def test_custom_top_n_button_waits_for_number(report_env):
    callback = make_callback(OWNER_ID, "report_gen:week:customtop")
    await h.cb_report_generate(callback)
    assert _shared.awaiting_report_topn[OWNER_ID] == "week"
    callback.message.edit_text.assert_awaited_once_with(t("report_custom_topn_prompt", "uk"))


@pytest.mark.asyncio
@pytest.mark.parametrize("data,expected_args", [
    ("report_gen:week:top5", ["week"]),
    ("report_gen:week:top10", ["week", "top10"]),
    ("report_gen:month:top15", ["top15"]),
    ("report_gen:year:full", ["year", "full"]),
    ("report_gen:today:top5", ["today"]),
    ("report_gen:2month:top10", ["2month", "top10"]),
])
async def test_generate_button_builds_the_right_report_arguments(report_env, monkeypatch, data, expected_args):
    generate = AsyncMock()
    monkeypatch.setattr(h, "_generate_report", generate)
    callback = make_callback(OWNER_ID, data)

    await h.cb_report_generate(callback)

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert generate.await_args.args[0] == OWNER_ID
    assert generate.await_args.args[1] == expected_args


# --- access control ---

@pytest.mark.asyncio
@pytest.mark.parametrize("handler,data", [
    ("cb_nav_report", "nav:report"),
    ("cb_report_period", "report_period:week"),
    ("cb_report_generate", "report_gen:week:top5"),
])
async def test_buttons_are_denied_for_strangers(report_env, handler, data):
    callback = make_callback(STRANGER_ID, data)
    await getattr(h, handler)(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_not_awaited()
