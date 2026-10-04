import pytest
from unittest.mock import AsyncMock

from core.handlers import edit as h
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data

ROW = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
ENTRY = {"row_index": 5, "date": "01.08.2026", "type_tr": "Expense",
         "category": "Кафе", "amount": 150, "description": "обід"}


@pytest.fixture
def storage(monkeypatch):
    mocks = {
        "get_recent_transactions_with_index": AsyncMock(return_value=([], False)),
        "get_transaction_row": AsyncMock(return_value=ROW),
        "delete_transaction_row": AsyncMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(h, name, mock)
    _shared.pending_edits.clear()
    _shared.awaiting_edit_field.clear()
    yield mocks
    _shared.pending_edits.clear()
    _shared.awaiting_edit_field.clear()


def _store_entry(entry=ENTRY) -> str:
    return _shared._store_pending_edit(dict(entry))


# --- small pure helpers ---

def test_entry_button_label_for_expense_and_income():
    assert h._entry_button_label(ROW) == "📉 01.08.2026 Кафе: 150"
    income = ["02.08.2026", "Income", "Зарплата", 20000, "-"]
    assert h._entry_button_label(income) == "💰 02.08.2026 Зарплата: 20000"


def test_entry_button_label_pads_short_rows():
    assert h._entry_button_label(["01.08.2026", "Expense", "Кафе"]) == "📉 01.08.2026 Кафе: -"


def test_rows_match_with_non_numeric_amount_compares_as_text():
    entry = dict(ENTRY, amount="n/a")
    assert h._rows_match(["01.08.2026", "Expense", "Кафе", "n/a", "обід"], entry) is True
    assert h._rows_match(["01.08.2026", "Expense", "Кафе", "other", "обід"], entry) is False


def test_edit_detail_text_contains_entry_fields():
    text = h._build_edit_detail_text(ENTRY, "uk")
    assert "Кафе" in text and "150" in text and "обід" in text


def test_edit_detail_text_for_income():
    text = h._build_edit_detail_text(dict(ENTRY, type_tr="Income"), "en")
    assert "💰" in text


def test_edit_detail_keyboard_has_all_actions():
    callbacks = keyboard_callback_data(h._build_edit_detail_keyboard("abc", "uk"))
    assert callbacks == ["edit_amount:abc", "edit_category:abc", "edit_desc:abc",
                         "edit_delete:abc", "edit_cancel:abc"]


# --- _show_edit_picker ---

@pytest.mark.asyncio
async def test_picker_reports_sheet_read_error(storage):
    storage["get_recent_transactions_with_index"].side_effect = RuntimeError("boom")
    answer = AsyncMock()
    await h._show_edit_picker(OWNER_ID, answer, "uk")
    assert "boom" in answer.await_args.args[0]


@pytest.mark.asyncio
async def test_picker_with_no_entries(storage):
    answer = AsyncMock()
    await h._show_edit_picker(OWNER_ID, answer, "uk")
    answer.assert_awaited_once_with(t("edit_no_entries", "uk"))


@pytest.mark.asyncio
async def test_picker_paged_past_the_oldest_entry_offers_way_back(storage):
    answer = AsyncMock()
    await h._show_edit_picker(OWNER_ID, answer, "uk", offset=20)
    assert answer.await_args.args[0] == t("edit_no_older_entries", "uk")
    assert keyboard_callback_data(answer.await_args.kwargs["reply_markup"]) == ["edit_page:10"]


@pytest.mark.asyncio
async def test_picker_lists_entries_most_recent_first(storage):
    older = ["01.08.2026", "Expense", "Кафе", 100, "A"]
    newer = ["02.08.2026", "Expense", "Таксі", 50, "B"]
    storage["get_recent_transactions_with_index"].return_value = ([(1, older), (2, newer)], False)
    answer = AsyncMock()

    await h._show_edit_picker(OWNER_ID, answer, "uk")

    markup = answer.await_args.kwargs["reply_markup"]
    labels = [row[0].text for row in markup.inline_keyboard]
    assert labels == ["📉 02.08.2026 Таксі: 50", "📉 01.08.2026 Кафе: 100"]
    # each button maps to a stored edit session pointing at the right sheet row
    first_id = keyboard_callback_data(markup)[0].split(":")[1]
    assert _shared.pending_edits[first_id]["row_index"] == 2


@pytest.mark.asyncio
async def test_picker_shows_older_button_when_more_pages_exist(storage):
    storage["get_recent_transactions_with_index"].return_value = ([(11, ROW)], True)
    answer = AsyncMock()
    await h._show_edit_picker(OWNER_ID, answer, "uk", offset=0)
    assert keyboard_callback_data(answer.await_args.kwargs["reply_markup"])[-1] == "edit_page:10"


@pytest.mark.asyncio
async def test_picker_shows_newer_and_older_buttons_on_middle_page(storage):
    storage["get_recent_transactions_with_index"].return_value = ([(5, ROW)], True)
    answer = AsyncMock()
    await h._show_edit_picker(OWNER_ID, answer, "uk", offset=10)
    nav_row = answer.await_args.kwargs["reply_markup"].inline_keyboard[-1]
    assert [b.callback_data for b in nav_row] == ["edit_page:0", "edit_page:20"]


# --- entry points ---

@pytest.mark.asyncio
async def test_edit_command_denied_for_stranger(storage):
    message = make_message(STRANGER_ID, "/edit")
    await h.cmd_edit(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    storage["get_recent_transactions_with_index"].assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_command_shows_picker(storage):
    message = make_message(OWNER_ID, "/edit")
    await h.cmd_edit(message)
    message.answer.assert_awaited_once_with(t("edit_no_entries", "uk"))


@pytest.mark.asyncio
async def test_nav_edit_shows_picker(storage):
    callback = make_callback(OWNER_ID, "nav:edit")
    await h.cb_nav_edit(callback)
    callback.message.answer.assert_awaited_once_with(t("edit_no_entries", "uk"))


@pytest.mark.asyncio
async def test_edit_page_button_edits_message_in_place(storage):
    storage["get_recent_transactions_with_index"].return_value = ([(1, ROW)], False)
    callback = make_callback(OWNER_ID, "edit_page:10")
    await h.cb_edit_page(callback)
    assert storage["get_recent_transactions_with_index"].await_args.kwargs["offset"] == 10
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_edit_page_button_tolerates_garbage_offset(storage):
    callback = make_callback(OWNER_ID, "edit_page:abc")
    await h.cb_edit_page(callback)
    assert storage["get_recent_transactions_with_index"].await_args.kwargs["offset"] == 0


# --- picking an entry ---

@pytest.mark.asyncio
async def test_pick_shows_entry_details(storage):
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_pick:{edit_id}")
    await h.cb_edit_pick(callback)
    assert "Кафе" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_pick_with_expired_session_alerts(storage):
    callback = make_callback(OWNER_ID, "edit_pick:gone")
    await h.cb_edit_pick(callback)
    assert callback.answer.await_args.args[0] == t("edit_expired", "uk")
    callback.message.edit_text.assert_not_awaited()


# --- choosing which field to edit ---

@pytest.mark.asyncio
@pytest.mark.parametrize("handler,prefix,field,prompt_key", [
    ("cb_edit_amount", "edit_amount", "amount", "edit_prompt_amount"),
    ("cb_edit_category", "edit_category", "category", "edit_prompt_category"),
    ("cb_edit_desc", "edit_desc", "description", "edit_prompt_description"),
])
async def test_field_buttons_wait_for_new_value(storage, handler, prefix, field, prompt_key):
    edit_id = _store_entry()
    _shared.awaiting_find_query[OWNER_ID] = True  # a stale flow that must be cleared
    callback = make_callback(OWNER_ID, f"{prefix}:{edit_id}")

    await getattr(h, handler)(callback)

    assert _shared.awaiting_edit_field[OWNER_ID] == (edit_id, field)
    assert OWNER_ID not in _shared.awaiting_find_query
    callback.message.edit_text.assert_awaited_once_with(t(prompt_key, "uk"))


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,prefix", [
    ("cb_edit_amount", "edit_amount"),
    ("cb_edit_category", "edit_category"),
    ("cb_edit_desc", "edit_desc"),
    ("cb_edit_delete", "edit_delete"),
    ("cb_edit_delete_confirm", "edit_delete_confirm"),
])
async def test_expired_sessions_are_rejected(storage, handler, prefix):
    callback = make_callback(OWNER_ID, f"{prefix}:gone")
    await getattr(h, handler)(callback)
    assert callback.answer.await_args.args[0] == t("edit_expired", "uk")
    assert OWNER_ID not in _shared.awaiting_edit_field


# --- delete flow ---

@pytest.mark.asyncio
async def test_delete_asks_for_confirmation(storage):
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete:{edit_id}")
    await h.cb_edit_delete(callback)
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert keyboard_callback_data(markup) == [f"edit_delete_confirm:{edit_id}", f"edit_pick:{edit_id}"]
    storage["delete_transaction_row"].assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_confirm_deletes_row(storage):
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete_confirm:{edit_id}")

    await h.cb_edit_delete_confirm(callback)

    storage["delete_transaction_row"].assert_awaited_once_with(OWNER_ID, 5)
    callback.message.edit_text.assert_awaited_once_with(t("edit_deleted", "uk"))
    assert edit_id not in _shared.pending_edits  # session consumed


@pytest.mark.asyncio
async def test_delete_confirm_aborts_when_row_changed(storage):
    storage["get_transaction_row"].return_value = ["09.09.2026", "Expense", "Інше", 1, "x"]
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete_confirm:{edit_id}")

    await h.cb_edit_delete_confirm(callback)

    storage["delete_transaction_row"].assert_not_awaited()  # must NOT delete the wrong row
    callback.message.edit_text.assert_awaited_once_with(t("edit_row_changed", "uk"))


@pytest.mark.asyncio
async def test_delete_confirm_reports_read_error(storage):
    storage["get_transaction_row"].side_effect = RuntimeError("boom")
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete_confirm:{edit_id}")
    await h.cb_edit_delete_confirm(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]
    storage["delete_transaction_row"].assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_confirm_reports_delete_error(storage):
    storage["delete_transaction_row"].side_effect = RuntimeError("boom")
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete_confirm:{edit_id}")
    await h.cb_edit_delete_confirm(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_delete_confirm_when_nothing_was_deleted(storage):
    storage["delete_transaction_row"].return_value = False
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_delete_confirm:{edit_id}")
    await h.cb_edit_delete_confirm(callback)
    callback.message.edit_text.assert_awaited_once_with(t("edit_delete_failed", "uk"))


# --- cancel ---

@pytest.mark.asyncio
async def test_cancel_discards_session(storage):
    edit_id = _store_entry()
    callback = make_callback(OWNER_ID, f"edit_cancel:{edit_id}")
    await h.cb_edit_cancel(callback)
    assert edit_id not in _shared.pending_edits
    callback.message.edit_text.assert_awaited_once_with(t("edit_cancelled", "uk"))


# --- access control ---

@pytest.mark.asyncio
@pytest.mark.parametrize("handler,data", [
    ("cb_nav_edit", "nav:edit"),
    ("cb_edit_page", "edit_page:0"),
    ("cb_edit_pick", "edit_pick:x"),
    ("cb_edit_amount", "edit_amount:x"),
    ("cb_edit_category", "edit_category:x"),
    ("cb_edit_desc", "edit_desc:x"),
    ("cb_edit_delete", "edit_delete:x"),
    ("cb_edit_delete_confirm", "edit_delete_confirm:x"),
])
async def test_buttons_are_denied_for_strangers(storage, handler, data):
    callback = make_callback(STRANGER_ID, data)
    await getattr(h, handler)(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.edit_text.assert_not_awaited()
    storage["delete_transaction_row"].assert_not_awaited()
