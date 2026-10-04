import pytest
from unittest.mock import AsyncMock

from core.handlers import find as h
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data

CAFE = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
TAXI = ["02.08.2026", "Expense", "Таксі", 75, "центр"]
CAFE2 = ["03.08.2026", "Expense", "Кафе", "100,50", "кава"]
SALARY = ["04.08.2026", "Income", "Зарплата", 20000, "серпень"]


def _patch_indexed(monkeypatch, rows=None, error=None):
    mock = AsyncMock(return_value=rows, side_effect=error)
    monkeypatch.setattr(h, "get_all_transactions_with_index", mock)
    return mock


def _patch_all(monkeypatch, rows=None, error=None):
    mock = AsyncMock(return_value=rows, side_effect=error)
    monkeypatch.setattr(h, "get_all_transactions", mock)
    return mock


@pytest.fixture(autouse=True)
def clean_pending_edits():
    _shared.pending_edits.clear()
    _shared._category_choices_cache.clear()
    yield
    _shared.pending_edits.clear()
    _shared._category_choices_cache.clear()


# --- _run_find ---

@pytest.mark.asyncio
async def test_run_find_reports_sheet_read_error(monkeypatch):
    _patch_indexed(monkeypatch, error=RuntimeError("boom"))
    answer = AsyncMock()
    await h._run_find(OWNER_ID, "кафе", answer)
    assert "boom" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_run_find_no_matches(monkeypatch):
    _patch_indexed(monkeypatch, [(1, CAFE)])
    answer = AsyncMock()
    await h._run_find(OWNER_ID, "неіснує", answer)
    answer.assert_awaited_once_with(t("find_no_results", "uk", query="неіснує"))


@pytest.mark.asyncio
async def test_run_find_lists_matches_total_and_edit_buttons(monkeypatch):
    _patch_indexed(monkeypatch, [(1, CAFE), (2, TAXI), (3, CAFE2)])
    answer = AsyncMock()

    await h._run_find(OWNER_ID, "кафе", answer)

    text = answer.await_args.args[0]
    assert "Знайдено 2" in text
    assert "250.50" in text  # 150 + "100,50" (comma decimal handled)
    # most recent first
    assert text.index("03.08.2026") < text.index("01.08.2026")
    markup = answer.await_args.kwargs["reply_markup"]
    callbacks = keyboard_callback_data(markup)
    assert len(callbacks) == 2 and all(c.startswith("edit_pick:") for c in callbacks)
    # each button points at a stored edit session for the right sheet row
    first_edit_id = callbacks[0].split(":")[1]
    assert _shared.pending_edits[first_edit_id]["row_index"] == 3


@pytest.mark.asyncio
async def test_run_find_matches_description_too(monkeypatch):
    _patch_indexed(monkeypatch, [(1, CAFE), (2, TAXI)])
    answer = AsyncMock()
    await h._run_find(OWNER_ID, "центр", answer)
    assert "Таксі" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_run_find_ignores_unparseable_amounts_in_total(monkeypatch):
    broken = ["01.08.2026", "Expense", "Кафе", "n/a", "x"]
    _patch_indexed(monkeypatch, [(1, broken), (2, CAFE)])
    answer = AsyncMock()
    await h._run_find(OWNER_ID, "кафе", answer)
    assert "150.00" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_run_find_pads_short_rows(monkeypatch):
    _patch_indexed(monkeypatch, [(1, ["01.08.2026", "Expense", "Кафе"])])
    answer = AsyncMock()
    await h._run_find(OWNER_ID, "кафе", answer)
    assert "Кафе" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_run_find_truncates_long_results_and_caps_buttons(monkeypatch):
    rows = [(i, ["01.08.2026", "Expense", "Кафе", 10, f"запис {i}"]) for i in range(1, 26)]
    _patch_indexed(monkeypatch, rows)
    answer = AsyncMock()

    await h._run_find(OWNER_ID, "кафе", answer)

    text = answer.await_args.args[0]
    assert "Знайдено 25" in text
    assert t("find_truncated_note", "uk", n=20) in text
    assert text.count("запис ") == 20  # only the 20 most recent are listed
    markup = answer.await_args.kwargs["reply_markup"]
    assert len(keyboard_callback_data(markup)) == h.MAX_EDIT_BUTTONS
    assert len(markup.inline_keyboard) == 2  # 10 buttons packed 5 per row


# --- /find ---

@pytest.mark.asyncio
async def test_find_denied_for_stranger(monkeypatch):
    fetch = _patch_indexed(monkeypatch, [])
    message = make_message(STRANGER_ID, "/find кафе")
    await h.cmd_find(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_with_query_runs_search(monkeypatch):
    _patch_indexed(monkeypatch, [(1, CAFE)])
    message = make_message(OWNER_ID, "/find кафе")
    await h.cmd_find(message)
    assert "Знайдено 1" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_find_without_query_offers_category_buttons(monkeypatch):
    _patch_all(monkeypatch, [CAFE, CAFE2, TAXI])
    message = make_message(OWNER_ID, "/find")

    await h.cmd_find(message)

    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert callbacks[-1] == "find_custom"
    assert callbacks[:-1] == ["find_cat:0", "find_cat:1"]
    # the picker remembers which category each index means
    assert _shared._get_category_choice(OWNER_ID, 0) == "Кафе"  # most used first


@pytest.mark.asyncio
async def test_find_without_query_and_no_data_shows_format_hint(monkeypatch):
    _patch_all(monkeypatch, [])
    message = make_message(OWNER_ID, "/find")
    await h.cmd_find(message)
    message.answer.assert_awaited_once_with(t("find_format_hint", "uk"))


@pytest.mark.asyncio
async def test_find_without_query_survives_sheet_error(monkeypatch):
    _patch_all(monkeypatch, error=RuntimeError("boom"))
    message = make_message(OWNER_ID, "/find   ")
    await h.cmd_find(message)
    message.answer.assert_awaited_once_with(t("find_format_hint", "uk"))


# --- category picker button ---

@pytest.mark.asyncio
async def test_find_category_button_searches_that_category(monkeypatch):
    _patch_indexed(monkeypatch, [(1, CAFE), (2, TAXI)])
    _shared._store_category_choices(OWNER_ID, ["Таксі", "Кафе"])
    callback = make_callback(OWNER_ID, "find_cat:0")

    await h.cb_find_category(callback)

    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    assert "Таксі" in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_find_category_button_with_stale_index_alerts(monkeypatch):
    fetch = _patch_indexed(monkeypatch, [])
    callback = make_callback(OWNER_ID, "find_cat:7")

    await h.cb_find_category(callback)

    assert callback.answer.await_args.args[0] == t("edit_expired", "uk")
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_category_button_denied_for_stranger():
    callback = make_callback(STRANGER_ID, "find_cat:0")
    await h.cb_find_category(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}


# --- custom text + nav buttons ---

@pytest.mark.asyncio
async def test_find_custom_waits_for_text():
    callback = make_callback(OWNER_ID, "find_custom")
    await h.cb_find_custom(callback)
    assert _shared.awaiting_find_query[OWNER_ID] is True
    callback.message.edit_text.assert_awaited_once_with(t("write_search_query", "uk"))


@pytest.mark.asyncio
async def test_find_custom_denied_for_stranger():
    callback = make_callback(STRANGER_ID, "find_custom")
    await h.cb_find_custom(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    assert STRANGER_ID not in _shared.awaiting_find_query


@pytest.mark.asyncio
async def test_nav_find_offers_category_buttons(monkeypatch):
    _patch_all(monkeypatch, [CAFE, TAXI])
    callback = make_callback(OWNER_ID, "nav:find")

    await h.cb_nav_find(callback)

    callbacks = keyboard_callback_data(callback.message.answer.await_args.kwargs["reply_markup"])
    assert callbacks[-1] == "find_custom" and len(callbacks) == 3


@pytest.mark.asyncio
async def test_nav_find_without_data_shows_format_hint(monkeypatch):
    _patch_all(monkeypatch, error=RuntimeError("boom"))
    callback = make_callback(OWNER_ID, "nav:find")
    await h.cb_nav_find(callback)
    callback.message.answer.assert_awaited_once_with(t("find_format_hint", "uk"))


@pytest.mark.asyncio
async def test_nav_find_denied_for_stranger():
    callback = make_callback(STRANGER_ID, "nav:find")
    await h.cb_nav_find(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.answer.assert_not_awaited()
