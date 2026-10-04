import pytest
from datetime import datetime
from unittest.mock import AsyncMock

from core.handlers import budget as h
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data


def _today_row(category, amount, type_tr="Expense"):
    return [datetime.now().strftime("%d.%m.%Y"), type_tr, category, amount, "-"]


@pytest.fixture
def storage(monkeypatch):
    """Replace every storage call the budget handlers use with AsyncMocks."""
    mocks = {
        "get_budgets": AsyncMock(return_value=[]),
        "get_all_transactions": AsyncMock(return_value=[]),
        "set_budget": AsyncMock(),
        "delete_budget": AsyncMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(h, name, mock)
    _shared._category_choices_cache.clear()
    yield mocks
    _shared._category_choices_cache.clear()
    _shared.awaiting_budget_amount.clear()
    _shared.awaiting_budget_category.clear()


# --- _show_budget_view ---

@pytest.mark.asyncio
async def test_view_when_no_limits_set(storage):
    answer = AsyncMock()
    await h._show_budget_view(OWNER_ID, answer)
    answer.assert_awaited_once_with(t("budget_not_set_yet", "uk"))
    storage["get_all_transactions"].assert_not_awaited()


@pytest.mark.asyncio
async def test_view_reports_budget_read_error(storage):
    storage["get_budgets"].side_effect = RuntimeError("boom")
    answer = AsyncMock()
    await h._show_budget_view(OWNER_ID, answer)
    assert "boom" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_view_reports_transactions_read_error(storage):
    storage["get_budgets"].return_value = [["Кафе", 1000]]
    storage["get_all_transactions"].side_effect = RuntimeError("tx boom")
    answer = AsyncMock()
    await h._show_budget_view(OWNER_ID, answer)
    assert "tx boom" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_view_shows_traffic_light_per_category(storage):
    storage["get_budgets"].return_value = [
        ["Кафе", 1000], ["Таксі", 100], ["Продукти", 1000], ["Одяг", 500],
    ]
    storage["get_all_transactions"].return_value = [
        _today_row("Кафе", 100),      # 10%  -> green
        _today_row("Таксі", 150),     # 150% -> red (over budget)
        _today_row("Продукти", 850),  # 85%  -> yellow
    ]
    answer = AsyncMock()

    await h._show_budget_view(OWNER_ID, answer)

    lines = answer.await_args.args[0].split("\n")
    by_category = {name: next(line for line in lines if f"<b>{name}:</b>" in line)
                   for name in ("Кафе", "Таксі", "Продукти", "Одяг")}
    assert by_category["Кафе"].startswith("🟢") and "100.00 / 1000.00" in by_category["Кафе"]
    assert by_category["Таксі"].startswith("🔴") and "(150%)" in by_category["Таксі"]
    assert by_category["Продукти"].startswith("🟡") and "(85%)" in by_category["Продукти"]
    assert by_category["Одяг"].startswith("🟢") and "(0%)" in by_category["Одяг"]


# --- /budget text command ---

@pytest.mark.asyncio
async def test_budget_denied_for_stranger(storage):
    message = make_message(STRANGER_ID, "/budget set Кафе 1000")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    storage["set_budget"].assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_without_args_shows_menu(storage):
    message = make_message(OWNER_ID, "/budget")
    await h.cmd_budget(message)
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert callbacks == ["budget_view", "budget_add", "budget_remove"]


@pytest.mark.asyncio
async def test_budget_set_saves_normalized_category(storage):
    message = make_message(OWNER_ID, "/budget set  кафе   1500,50")
    await h.cmd_budget(message)
    storage["set_budget"].assert_awaited_once_with(OWNER_ID, "Кафе", 1500.5)
    message.answer.assert_awaited_once_with(t("budget_limit_set", "uk", category="Кафе", limit=1500.5))


@pytest.mark.asyncio
async def test_budget_set_supports_multi_word_categories(storage):
    message = make_message(OWNER_ID, "/budget set Продукти для дому 3000")
    await h.cmd_budget(message)
    assert storage["set_budget"].await_args.args[2] == 3000.0
    assert "Продукти" in storage["set_budget"].await_args.args[1]


@pytest.mark.asyncio
async def test_budget_set_missing_amount_shows_format(storage):
    message = make_message(OWNER_ID, "/budget set Кафе")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("budget_format_set", "uk"))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_amount", ["abc", "0", "-5"])
async def test_budget_set_rejects_bad_amounts(storage, bad_amount):
    message = make_message(OWNER_ID, f"/budget set Кафе {bad_amount}")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("budget_bad_amount", "uk"))
    storage["set_budget"].assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_set_reports_write_error(storage):
    storage["set_budget"].side_effect = RuntimeError("boom")
    message = make_message(OWNER_ID, "/budget set Кафе 1000")
    await h.cmd_budget(message)
    assert "boom" in message.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["remove", "delete", "видалити"])
async def test_budget_remove_existing(storage, word):
    message = make_message(OWNER_ID, f"/budget {word} Кафе")
    await h.cmd_budget(message)
    storage["delete_budget"].assert_awaited_once_with(OWNER_ID, "Кафе")
    message.answer.assert_awaited_once_with(t("budget_limit_removed", "uk", category="Кафе"))


@pytest.mark.asyncio
async def test_budget_remove_not_found(storage):
    storage["delete_budget"].return_value = False
    message = make_message(OWNER_ID, "/budget remove Кафе")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("budget_limit_not_found", "uk", category="Кафе"))


@pytest.mark.asyncio
async def test_budget_remove_without_category_shows_format(storage):
    message = make_message(OWNER_ID, "/budget remove")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("budget_format_remove", "uk"))


@pytest.mark.asyncio
async def test_budget_remove_reports_error(storage):
    storage["delete_budget"].side_effect = RuntimeError("boom")
    message = make_message(OWNER_ID, "/budget remove Кафе")
    await h.cmd_budget(message)
    assert "boom" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_budget_unknown_subcommand(storage):
    message = make_message(OWNER_ID, "/budget banana")
    await h.cmd_budget(message)
    message.answer.assert_awaited_once_with(t("budget_unknown_command", "uk"))


# --- inline buttons ---

@pytest.mark.asyncio
async def test_view_button_removes_keyboard_and_shows_budget(storage):
    callback = make_callback(OWNER_ID, "budget_view")
    await h.cb_budget_view(callback)
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    callback.message.answer.assert_awaited_once_with(t("budget_not_set_yet", "uk"))


@pytest.mark.asyncio
async def test_add_button_offers_most_used_categories(storage):
    storage["get_all_transactions"].return_value = [
        _today_row("Кафе", 1), _today_row("Кафе", 2), _today_row("Таксі", 3),
    ]
    callback = make_callback(OWNER_ID, "budget_add")

    await h.cb_budget_add(callback)

    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert callbacks == ["budget_set_cat:0", "budget_set_cat:1", "budget_set_cat_custom"]
    assert _shared._get_category_choice(OWNER_ID, 0) == "Кафе"


@pytest.mark.asyncio
async def test_add_button_falls_back_to_defaults_for_new_user(storage):
    callback = make_callback(OWNER_ID, "budget_add")
    await h.cb_budget_add(callback)
    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert len(callbacks) > 1 and callbacks[-1] == "budget_set_cat_custom"


@pytest.mark.asyncio
async def test_add_button_survives_sheet_error(storage):
    storage["get_all_transactions"].side_effect = RuntimeError("boom")
    callback = make_callback(OWNER_ID, "budget_add")
    await h.cb_budget_add(callback)
    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert callbacks == ["budget_set_cat_custom"]


@pytest.mark.asyncio
async def test_pick_category_waits_for_amount(storage):
    _shared._store_category_choices(OWNER_ID, ["Кафе", "Таксі"])
    callback = make_callback(OWNER_ID, "budget_set_cat:1")

    await h.cb_budget_set_category(callback)

    assert _shared.awaiting_budget_amount[OWNER_ID] == "Таксі"
    callback.message.edit_text.assert_awaited_once_with(t("write_budget_amount", "uk", category="Таксі"))


@pytest.mark.asyncio
async def test_pick_category_with_stale_index_alerts(storage):
    callback = make_callback(OWNER_ID, "budget_set_cat:5")
    await h.cb_budget_set_category(callback)
    assert callback.answer.await_args.args[0] == t("edit_expired", "uk")
    assert OWNER_ID not in _shared.awaiting_budget_amount


@pytest.mark.asyncio
async def test_custom_category_waits_for_name(storage):
    callback = make_callback(OWNER_ID, "budget_set_cat_custom")
    await h.cb_budget_set_category_custom(callback)
    assert _shared.awaiting_budget_category[OWNER_ID] is True
    callback.message.edit_text.assert_awaited_once_with(t("write_category_name", "uk"))


@pytest.mark.asyncio
async def test_remove_button_lists_budgets_sorted(storage):
    storage["get_budgets"].return_value = [["Таксі", 500], ["Кафе", 1000]]
    callback = make_callback(OWNER_ID, "budget_remove")

    await h.cb_budget_remove(callback)

    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert keyboard_callback_data(markup) == ["budget_del_cat:0", "budget_del_cat:1"]
    assert markup.inline_keyboard[0][0].text == "Кафе (1000 грн)"
    assert _shared._get_category_choice(OWNER_ID, 1) == "Таксі"


@pytest.mark.asyncio
async def test_remove_button_with_no_budgets(storage):
    callback = make_callback(OWNER_ID, "budget_remove")
    await h.cb_budget_remove(callback)
    callback.message.edit_text.assert_awaited_once_with(t("budget_none_to_remove", "uk"))


@pytest.mark.asyncio
async def test_remove_button_reports_read_error(storage):
    storage["get_budgets"].side_effect = RuntimeError("boom")
    callback = make_callback(OWNER_ID, "budget_remove")
    await h.cb_budget_remove(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_category_button_deletes(storage):
    _shared._store_category_choices(OWNER_ID, ["Кафе"])
    callback = make_callback(OWNER_ID, "budget_del_cat:0")

    await h.cb_budget_delete_category(callback)

    storage["delete_budget"].assert_awaited_once_with(OWNER_ID, "Кафе")
    callback.answer.assert_awaited_once_with(t("toast_deleted", "uk"))
    callback.message.edit_text.assert_awaited_once_with(t("budget_limit_removed", "uk", category="Кафе"))


@pytest.mark.asyncio
async def test_delete_category_button_when_already_gone(storage):
    storage["delete_budget"].return_value = False
    _shared._store_category_choices(OWNER_ID, ["Кафе"])
    callback = make_callback(OWNER_ID, "budget_del_cat:0")

    await h.cb_budget_delete_category(callback)

    callback.answer.assert_awaited_once_with(t("toast_not_found", "uk"))
    callback.message.edit_text.assert_awaited_once_with(t("budget_limit_not_found", "uk", category="Кафе"))


@pytest.mark.asyncio
async def test_delete_category_button_with_stale_index(storage):
    callback = make_callback(OWNER_ID, "budget_del_cat:3")
    await h.cb_budget_delete_category(callback)
    assert callback.answer.await_args.args[0] == t("edit_expired", "uk")
    storage["delete_budget"].assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_category_button_reports_error(storage):
    storage["delete_budget"].side_effect = RuntimeError("boom")
    _shared._store_category_choices(OWNER_ID, ["Кафе"])
    callback = make_callback(OWNER_ID, "budget_del_cat:0")
    await h.cb_budget_delete_category(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_nav_budget_shows_menu(storage):
    callback = make_callback(OWNER_ID, "nav:budget")
    await h.cb_nav_budget(callback)
    assert keyboard_callback_data(callback.message.answer.await_args.kwargs["reply_markup"]) == [
        "budget_view", "budget_add", "budget_remove"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,data", [
    ("cb_budget_view", "budget_view"),
    ("cb_budget_add", "budget_add"),
    ("cb_budget_set_category", "budget_set_cat:0"),
    ("cb_budget_set_category_custom", "budget_set_cat_custom"),
    ("cb_budget_remove", "budget_remove"),
    ("cb_budget_delete_category", "budget_del_cat:0"),
    ("cb_nav_budget", "nav:budget"),
])
async def test_every_button_is_denied_for_strangers(storage, handler, data):
    callback = make_callback(STRANGER_ID, data)
    await getattr(h, handler)(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_not_awaited()
    storage["delete_budget"].assert_not_awaited()
