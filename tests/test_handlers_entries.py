import pytest
from unittest.mock import AsyncMock

from core import reminder
from core.handlers import entries as e
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data

ROW = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
ROW2 = ["02.08.2026", "Income", "Зарплата", 20000, "серпень"]
ENTRY = {"date": "2026-08-01", "type_tr": "Expense", "category": "Кафе",
         "amount": 150.0, "description": "обід", "is_duplicate": False}
EDIT = {"row_index": 5, "date": "01.08.2026", "type_tr": "Expense",
        "category": "Кафе", "amount": 150, "description": "обід"}


@pytest.fixture
def storage(monkeypatch):
    """Every storage call the entries handlers make, replaced by AsyncMocks."""
    mocks = {
        "append_transaction": AsyncMock(),
        "append_transactions_batch": AsyncMock(),
        "get_last_transaction": AsyncMock(return_value=ROW),
        "delete_last_transaction": AsyncMock(return_value=ROW),
        "get_all_transactions": AsyncMock(return_value=[]),
        "get_last_n_transactions": AsyncMock(return_value=[ROW, ROW2]),
        "delete_last_n_transactions": AsyncMock(return_value=[ROW, ROW2]),
        "set_budget": AsyncMock(),
        "update_transaction_row": AsyncMock(),
        "get_transaction_row": AsyncMock(return_value=ROW),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(e, name, mock)
    _reset_state()
    yield mocks
    _reset_state()


def _reset_state():
    for d in (_shared.pending_entries, _shared.pending_batches, _shared.pending_edits,
              _shared._last_action_count, _shared._undo_snapshot, _shared.recent_entries,
              _shared._category_choices_cache, *_shared._ALL_AWAITING_DICTS):
        d.clear()


def _pending_entry(**overrides) -> str:
    return _shared._store_pending_entry({**ENTRY, **overrides})


def _pending_edit(**overrides) -> str:
    return _shared._store_pending_edit({**EDIT, **overrides})


def _denied(callback) -> bool:
    return callback.answer.await_args.kwargs == {"show_alert": True}


# =============================== /last ===============================

@pytest.mark.asyncio
async def test_last_denied_for_stranger(storage):
    message = make_message(STRANGER_ID, "/last")
    await e.cmd_last(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    storage["get_last_transaction"].assert_not_awaited()


@pytest.mark.asyncio
async def test_last_reports_read_error(storage):
    storage["get_last_transaction"].side_effect = RuntimeError("boom")
    message = make_message(OWNER_ID, "/last")
    await e.cmd_last(message)
    assert "boom" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_last_with_no_entries(storage):
    storage["get_last_transaction"].return_value = None
    message = make_message(OWNER_ID, "/last")
    await e.cmd_last(message)
    message.answer.assert_awaited_once_with(t("no_entries_yet", "uk"))


@pytest.mark.asyncio
async def test_last_shows_the_entry(storage):
    message = make_message(OWNER_ID, "/last")
    await e.cmd_last(message)
    text = message.answer.await_args.args[0]
    assert "📉" in text and "Кафе" in text and "150" in text and "обід" in text


@pytest.mark.asyncio
async def test_last_shows_income_icon_and_pads_short_rows(storage):
    storage["get_last_transaction"].return_value = ["02.08.2026", "Income", "Зарплата"]
    message = make_message(OWNER_ID, "/last")
    await e.cmd_last(message)
    text = message.answer.await_args.args[0]
    assert "💰" in text and "Зарплата" in text


@pytest.mark.asyncio
async def test_nav_last_variants(storage):
    callback = make_callback(STRANGER_ID, "nav:last")
    await e.cb_nav_last(callback)
    assert _denied(callback)

    storage["get_last_transaction"].side_effect = RuntimeError("boom")
    callback = make_callback(OWNER_ID, "nav:last")
    await e.cb_nav_last(callback)
    assert "boom" in callback.message.answer.await_args.args[0]

    storage["get_last_transaction"].side_effect = None
    storage["get_last_transaction"].return_value = None
    callback = make_callback(OWNER_ID, "nav:last")
    await e.cb_nav_last(callback)
    callback.message.answer.assert_awaited_once_with(t("no_entries_yet", "uk"))

    storage["get_last_transaction"].return_value = ROW
    callback = make_callback(OWNER_ID, "nav:last")
    await e.cb_nav_last(callback)
    assert "Кафе" in callback.message.answer.await_args.args[0]


# =============================== /undo ===============================

@pytest.mark.asyncio
async def test_undo_denied_for_stranger(storage):
    message = make_message(STRANGER_ID, "/undo")
    await e.cmd_undo(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


@pytest.mark.asyncio
async def test_undo_single_row_asks_for_confirmation_and_snapshots_it(storage):
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert callbacks == ["undo_confirm", "undo_cancel"]
    assert _shared._undo_snapshot[OWNER_ID] == {"row": ROW}


@pytest.mark.asyncio
async def test_undo_single_row_read_error_and_empty(storage):
    storage["get_last_transaction"].side_effect = RuntimeError("boom")
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    assert "boom" in message.answer.await_args.args[0]

    storage["get_last_transaction"].side_effect = None
    storage["get_last_transaction"].return_value = None
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    message.answer.assert_awaited_once_with(t("no_entries_to_delete", "uk"))


@pytest.mark.asyncio
async def test_undo_after_batch_offers_to_delete_the_whole_batch(storage):
    _shared._last_action_count[OWNER_ID] = 2
    message = make_message(OWNER_ID, "/undo")

    await e.cmd_undo(message)

    text = message.answer.await_args.args[0]
    assert "Кафе: 150" in text and "Зарплата: 20000" in text
    assert t("total_label", "uk", total=20150.0) in text
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert callbacks == ["undo_batch_confirm:2", "undo_cancel"]
    assert _shared._undo_snapshot[OWNER_ID] == {"rows": [ROW, ROW2]}


@pytest.mark.asyncio
async def test_undo_batch_ignores_unparseable_amounts_in_total(storage):
    storage["get_last_n_transactions"].return_value = [["d", "Expense", "Кафе", "n/a", "x"], ROW]
    _shared._last_action_count[OWNER_ID] = 2
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    assert t("total_label", "uk", total=150.0) in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_undo_batch_read_error_and_empty(storage):
    _shared._last_action_count[OWNER_ID] = 2
    storage["get_last_n_transactions"].side_effect = RuntimeError("boom")
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    assert "boom" in message.answer.await_args.args[0]

    storage["get_last_n_transactions"].side_effect = None
    storage["get_last_n_transactions"].return_value = []
    message = make_message(OWNER_ID, "/undo")
    await e.cmd_undo(message)
    message.answer.assert_awaited_once_with(t("no_entries_to_delete", "uk"))


@pytest.mark.asyncio
async def test_nav_undo_variants(storage):
    callback = make_callback(STRANGER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    assert _denied(callback)

    # single row
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    assert keyboard_callback_data(callback.message.answer.await_args.kwargs["reply_markup"]) == [
        "undo_confirm", "undo_cancel"]

    storage["get_last_transaction"].side_effect = RuntimeError("boom")
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    assert "boom" in callback.message.answer.await_args.args[0]

    storage["get_last_transaction"].side_effect = None
    storage["get_last_transaction"].return_value = None
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    callback.message.answer.assert_awaited_once_with(t("no_entries_to_delete", "uk"))

    # batch
    _shared._last_action_count[OWNER_ID] = 2
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    assert keyboard_callback_data(callback.message.answer.await_args.kwargs["reply_markup"]) == [
        "undo_batch_confirm:2", "undo_cancel"]

    storage["get_last_n_transactions"].side_effect = RuntimeError("boom")
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    assert "boom" in callback.message.answer.await_args.args[0]

    storage["get_last_n_transactions"].side_effect = None
    storage["get_last_n_transactions"].return_value = []
    callback = make_callback(OWNER_ID, "nav:undo")
    await e.cb_nav_undo(callback)
    callback.message.answer.assert_awaited_once_with(t("no_entries_to_delete", "uk"))


# --- undo confirmation buttons ---

@pytest.mark.asyncio
async def test_undo_confirm_denied_for_stranger(storage):
    callback = make_callback(STRANGER_ID, "undo_confirm")
    await e.cb_undo_confirm(callback)
    assert _denied(callback)
    storage["delete_last_transaction"].assert_not_awaited()


@pytest.mark.asyncio
async def test_undo_confirm_happy_path(storage):
    _shared._undo_snapshot[OWNER_ID] = {"row": ROW}
    callback = make_callback(OWNER_ID, "undo_confirm")
    await e.cb_undo_confirm(callback)
    storage["delete_last_transaction"].assert_awaited_once_with(OWNER_ID)
    callback.message.edit_text.assert_awaited_once_with(t("entry_deleted", "uk"))


@pytest.mark.asyncio
async def test_undo_confirm_errors(storage):
    _shared._undo_snapshot[OWNER_ID] = {"row": ROW}
    storage["get_last_transaction"].side_effect = RuntimeError("read boom")
    callback = make_callback(OWNER_ID, "undo_confirm")
    await e.cb_undo_confirm(callback)
    assert "read boom" in callback.message.edit_text.await_args.args[0]

    storage["get_last_transaction"].side_effect = None
    _shared._undo_snapshot[OWNER_ID] = {"row": ROW}
    storage["delete_last_transaction"].side_effect = RuntimeError("delete boom")
    callback = make_callback(OWNER_ID, "undo_confirm")
    await e.cb_undo_confirm(callback)
    assert "delete boom" in callback.message.edit_text.await_args.args[0]

    storage["delete_last_transaction"].side_effect = None
    storage["delete_last_transaction"].return_value = None
    _shared._undo_snapshot[OWNER_ID] = {"row": ROW}
    callback = make_callback(OWNER_ID, "undo_confirm")
    await e.cb_undo_confirm(callback)
    callback.message.edit_text.assert_awaited_once_with(t("no_entries_to_delete_short", "uk"))


@pytest.mark.asyncio
async def test_undo_batch_confirm_happy_path_resets_batch_counter(storage):
    _shared._undo_snapshot[OWNER_ID] = {"rows": [ROW, ROW2]}
    _shared._last_action_count[OWNER_ID] = 2
    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")

    await e.cb_undo_batch_confirm(callback)

    storage["delete_last_n_transactions"].assert_awaited_once_with(OWNER_ID, 2)
    assert _shared._last_action_count[OWNER_ID] == 1
    callback.message.edit_text.assert_awaited_once_with(t("batch_deleted", "uk", n=2))


@pytest.mark.asyncio
async def test_undo_batch_confirm_aborts_when_rows_changed_or_snapshot_missing(storage):
    _shared._undo_snapshot[OWNER_ID] = {"rows": [ROW, ["x", "Expense", "Інше", 1, "-"]]}
    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")
    await e.cb_undo_batch_confirm(callback)
    callback.message.edit_text.assert_awaited_once_with(t("undo_row_changed", "uk"))

    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")  # snapshot already consumed
    await e.cb_undo_batch_confirm(callback)
    callback.message.edit_text.assert_awaited_once_with(t("undo_row_changed", "uk"))
    storage["delete_last_n_transactions"].assert_not_awaited()


@pytest.mark.asyncio
async def test_undo_batch_confirm_errors(storage):
    callback = make_callback(STRANGER_ID, "undo_batch_confirm:2")
    await e.cb_undo_batch_confirm(callback)
    assert _denied(callback)

    _shared._undo_snapshot[OWNER_ID] = {"rows": [ROW, ROW2]}
    storage["get_last_n_transactions"].side_effect = RuntimeError("read boom")
    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")
    await e.cb_undo_batch_confirm(callback)
    assert "read boom" in callback.message.edit_text.await_args.args[0]

    storage["get_last_n_transactions"].side_effect = None
    _shared._undo_snapshot[OWNER_ID] = {"rows": [ROW, ROW2]}
    storage["delete_last_n_transactions"].side_effect = RuntimeError("delete boom")
    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")
    await e.cb_undo_batch_confirm(callback)
    assert "delete boom" in callback.message.edit_text.await_args.args[0]

    storage["delete_last_n_transactions"].side_effect = None
    storage["delete_last_n_transactions"].return_value = []
    _shared._undo_snapshot[OWNER_ID] = {"rows": [ROW, ROW2]}
    callback = make_callback(OWNER_ID, "undo_batch_confirm:2")
    await e.cb_undo_batch_confirm(callback)
    callback.message.edit_text.assert_awaited_once_with(t("no_entries_to_delete_short", "uk"))


@pytest.mark.asyncio
async def test_undo_cancel_discards_snapshot(storage):
    _shared._undo_snapshot[OWNER_ID] = {"row": ROW}
    callback = make_callback(OWNER_ID, "undo_cancel")
    await e.cb_undo_cancel(callback)
    assert OWNER_ID not in _shared._undo_snapshot
    callback.message.edit_text.assert_awaited_once_with(t("undo_cancelled", "uk"))


# ======================= pending entry: confirm / cancel =======================

@pytest.mark.asyncio
async def test_entry_confirm_saves_and_records_it(storage):
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"entry_confirm:{entry_id}")

    await e.cb_entry_confirm(callback)

    storage["append_transaction"].assert_awaited_once_with(
        OWNER_ID, date="2026-08-01", type_tr="Expense", category="Кафе", amount=150.0, description="обід")
    assert entry_id not in _shared.pending_entries
    assert _shared._last_action_count[OWNER_ID] == 1
    assert _shared._is_likely_duplicate(OWNER_ID, "Expense", "Кафе", 150.0) is True
    assert "Кафе" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_entry_confirm_saves_income_with_income_icon(storage):
    entry_id = _pending_entry(type_tr="Income", category="Зарплата")
    callback = make_callback(OWNER_ID, f"entry_confirm:{entry_id}")
    await e.cb_entry_confirm(callback)
    assert "💰" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_entry_confirm_expired(storage):
    callback = make_callback(OWNER_ID, "entry_confirm:gone")
    await e.cb_entry_confirm(callback)
    assert callback.answer.await_args.args[0] == t("entry_expired", "uk")
    storage["append_transaction"].assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_confirm_denied_for_stranger(storage):
    entry_id = _pending_entry()
    callback = make_callback(STRANGER_ID, f"entry_confirm:{entry_id}")
    await e.cb_entry_confirm(callback)
    assert _denied(callback)
    storage["append_transaction"].assert_not_awaited()


@pytest.mark.asyncio
async def test_entry_confirm_write_error(storage):
    storage["append_transaction"].side_effect = RuntimeError("boom")
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"entry_confirm:{entry_id}")
    await e.cb_entry_confirm(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_entry_cancel_discards_the_pending_entry(storage):
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"entry_cancel:{entry_id}")
    await e.cb_entry_cancel(callback)
    assert entry_id not in _shared.pending_entries
    callback.message.edit_text.assert_awaited_once_with(t("entry_cancelled", "uk"))


# ======================= pending entry: category editing =======================

@pytest.mark.asyncio
async def test_edit_category_offers_frequent_categories(storage):
    storage["get_all_transactions"].return_value = [
        ["d", "Expense", "Кафе", 1, "-"], ["d", "Expense", "Кафе", 2, "-"], ["d", "Expense", "Таксі", 3, "-"],
    ]
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"entry_edit_cat:{entry_id}")

    await e.cb_entry_edit_category(callback)

    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert callbacks == [f"set_cat:{entry_id}:0", f"set_cat:{entry_id}:1",
                         f"custom_cat:{entry_id}", f"back_to_preview:{entry_id}"]
    assert _shared.pending_entries[entry_id]["_category_choices"] == ["Кафе", "Таксі"]


@pytest.mark.asyncio
async def test_edit_category_survives_sheet_error(storage):
    storage["get_all_transactions"].side_effect = RuntimeError("boom")
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"entry_edit_cat:{entry_id}")
    await e.cb_entry_edit_category(callback)
    callbacks = keyboard_callback_data(callback.message.edit_text.await_args.kwargs["reply_markup"])
    assert callbacks == [f"custom_cat:{entry_id}", f"back_to_preview:{entry_id}"]


@pytest.mark.asyncio
async def test_edit_category_expired_and_denied(storage):
    callback = make_callback(OWNER_ID, "entry_edit_cat:gone")
    await e.cb_entry_edit_category(callback)
    assert callback.answer.await_args.args[0] == t("entry_expired", "uk")

    callback = make_callback(STRANGER_ID, "entry_edit_cat:x")
    await e.cb_entry_edit_category(callback)
    assert _denied(callback)


@pytest.mark.asyncio
async def test_set_category_applies_the_chosen_category(storage):
    entry_id = _pending_entry(_category_choices=["Таксі", "Кафе"])
    callback = make_callback(OWNER_ID, f"set_cat:{entry_id}:0")
    await e.cb_set_category(callback)
    assert _shared.pending_entries[entry_id]["category"] == "Таксі"
    assert "Таксі" in callback.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("idx", ["9", "abc"])
async def test_set_category_with_bad_index_keeps_current_category(storage, idx):
    entry_id = _pending_entry(_category_choices=["Таксі"])
    callback = make_callback(OWNER_ID, f"set_cat:{entry_id}:{idx}")
    await e.cb_set_category(callback)
    assert _shared.pending_entries[entry_id]["category"] == "Кафе"
    assert _denied(callback)  # stale-tap alert


@pytest.mark.asyncio
async def test_set_category_expired_and_denied(storage):
    callback = make_callback(OWNER_ID, "set_cat:gone:0")
    await e.cb_set_category(callback)
    assert callback.answer.await_args.args[0] == t("entry_expired_short", "uk")

    callback = make_callback(STRANGER_ID, "set_cat:x:0")
    await e.cb_set_category(callback)
    assert callback.answer.await_args.args[0] == t("access_denied", "uk")


@pytest.mark.asyncio
async def test_custom_category_waits_for_text(storage):
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"custom_cat:{entry_id}")
    await e.cb_custom_category(callback)
    assert _shared.awaiting_category_text[OWNER_ID] == entry_id
    callback.message.edit_text.assert_awaited_once_with(t("write_new_category", "uk"))


@pytest.mark.asyncio
async def test_custom_category_expired_and_denied(storage):
    callback = make_callback(OWNER_ID, "custom_cat:gone")
    await e.cb_custom_category(callback)
    assert OWNER_ID not in _shared.awaiting_category_text

    callback = make_callback(STRANGER_ID, "custom_cat:x")
    await e.cb_custom_category(callback)
    assert callback.answer.await_args.args[0] == t("access_denied", "uk")


@pytest.mark.asyncio
async def test_back_to_preview(storage):
    entry_id = _pending_entry()
    callback = make_callback(OWNER_ID, f"back_to_preview:{entry_id}")
    await e.cb_back_to_preview(callback)
    assert "Кафе" in callback.message.edit_text.await_args.args[0]

    callback = make_callback(OWNER_ID, "back_to_preview:gone")
    await e.cb_back_to_preview(callback)
    assert callback.answer.await_args.args[0] == t("entry_expired_short", "uk")


# ============================ batch confirm / cancel ============================

BATCH = [
    {"date": "2026-08-01", "type_tr": "Expense", "category": "Кафе", "amount": 150.0, "description": "-"},
    {"date": "2026-08-01", "type_tr": "Income", "category": "Зарплата", "amount": 20000.0, "description": "-"},
]


@pytest.mark.asyncio
async def test_batch_confirm_saves_everything_in_one_call(storage):
    batch_id = _shared._store_pending_batch(list(BATCH))
    callback = make_callback(OWNER_ID, f"batch_confirm:{batch_id}")

    await e.cb_batch_confirm(callback)

    storage["append_transactions_batch"].assert_awaited_once_with(OWNER_ID, BATCH)
    assert _shared._last_action_count[OWNER_ID] == 2
    assert _shared._is_likely_duplicate(OWNER_ID, "Income", "Зарплата", 20000.0) is True
    callback.message.edit_text.assert_awaited_once_with(t("batch_saved_all", "uk", n=2))


@pytest.mark.asyncio
async def test_batch_confirm_expired_denied_and_error(storage):
    callback = make_callback(OWNER_ID, "batch_confirm:gone")
    await e.cb_batch_confirm(callback)
    assert callback.answer.await_args.args[0] == t("entries_expired", "uk")

    callback = make_callback(STRANGER_ID, "batch_confirm:x")
    await e.cb_batch_confirm(callback)
    assert _denied(callback)

    storage["append_transactions_batch"].side_effect = RuntimeError("boom")
    batch_id = _shared._store_pending_batch(list(BATCH))
    callback = make_callback(OWNER_ID, f"batch_confirm:{batch_id}")
    await e.cb_batch_confirm(callback)
    assert "boom" in callback.message.edit_text.await_args.args[0]
    assert OWNER_ID not in _shared._last_action_count


@pytest.mark.asyncio
async def test_batch_cancel(storage):
    batch_id = _shared._store_pending_batch(list(BATCH))
    callback = make_callback(OWNER_ID, f"batch_cancel:{batch_id}")
    await e.cb_batch_cancel(callback)
    assert batch_id not in _shared.pending_batches
    callback.message.edit_text.assert_awaited_once_with(t("batch_cancelled", "uk"))


# ================== free text: access + new transactions ==================

@pytest.mark.asyncio
async def test_free_text_denied_for_stranger(storage):
    message = make_message(STRANGER_ID, "150 Кафе")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    assert not _shared.pending_entries


@pytest.mark.asyncio
async def test_single_line_creates_a_pending_entry_preview(storage):
    message = make_message(OWNER_ID, "150 Кафе обід")

    await e.handle_financial_entry(message)

    (entry,) = _shared.pending_entries.values()
    assert (entry["type_tr"], entry["category"], entry["amount"], entry["description"]) == (
        "Expense", "Кафе", 150.0, "обід")
    assert entry["is_duplicate"] is False
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert [c.split(":")[0] for c in callbacks] == ["entry_confirm", "entry_cancel", "entry_edit_cat"]


@pytest.mark.asyncio
async def test_repeating_a_recent_entry_flags_a_duplicate(storage):
    _shared._record_recent_entry(OWNER_ID, "Expense", "Кафе", 150.0)
    message = make_message(OWNER_ID, "150 Кафе")
    await e.handle_financial_entry(message)
    (entry,) = _shared.pending_entries.values()
    assert entry["is_duplicate"] is True
    assert t("duplicate_warning", "uk") in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_unrecognized_single_line(storage):
    message = make_message(OWNER_ID, "привіт")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("unrecognized_format", "uk"))
    assert not _shared.pending_entries


@pytest.mark.asyncio
async def test_multi_line_creates_a_batch_preview_listing_failed_lines(storage):
    message = make_message(OWNER_ID, "150 Кафе\n25000 Зарплата червень\nнісенітниця")

    await e.handle_financial_entry(message)

    (batch,) = _shared.pending_batches.values()
    assert [x["category"] for x in batch] == ["Кафе", "Зарплата"]
    text = message.answer.await_args.args[0]
    assert "• нісенітниця" in text
    callbacks = keyboard_callback_data(message.answer.await_args.kwargs["reply_markup"])
    assert [c.split(":")[0] for c in callbacks] == ["batch_confirm", "batch_cancel"]


@pytest.mark.asyncio
async def test_multi_line_with_nothing_recognized(storage):
    message = make_message(OWNER_ID, "нісенітниця\nще щось")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("unrecognized_lines", "uk"))
    assert not _shared.pending_batches


# ============================ resumable flows ============================

@pytest.mark.asyncio
@pytest.mark.parametrize("text,expected_args", [(".", []), ("6 2026", ["6", "2026"]), ("week top3", ["week", "top3"])])
async def test_custom_report_period_text_generates_report(storage, monkeypatch, text, expected_args):
    generate = AsyncMock()
    monkeypatch.setattr(e, "_generate_report", generate)
    _shared.awaiting_report_args[OWNER_ID] = True

    await e.handle_financial_entry(make_message(OWNER_ID, text))

    assert generate.await_args.args[1] == expected_args
    assert OWNER_ID not in _shared.awaiting_report_args
    assert not _shared.pending_entries  # not treated as a transaction


@pytest.mark.asyncio
async def test_custom_top_n_text(storage, monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(e, "_generate_report", generate)
    _shared.awaiting_report_topn[OWNER_ID] = "week"
    await e.handle_financial_entry(make_message(OWNER_ID, "7"))
    assert generate.await_args.args[1] == ["week", "top7"]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["abc", "0", "-3"])
async def test_custom_top_n_rejects_bad_numbers(storage, monkeypatch, text):
    generate = AsyncMock()
    monkeypatch.setattr(e, "_generate_report", generate)
    _shared.awaiting_report_topn[OWNER_ID] = "week"
    message = make_message(OWNER_ID, text)
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("int_over_zero_prompt", "uk"))
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_remind_time_text_adds_a_time(storage):
    _shared.awaiting_remind_time[OWNER_ID] = True
    message = make_message(OWNER_ID, "9:30")
    await e.handle_financial_entry(message)
    assert "09:30" in reminder.get_times()
    assert message.answer.await_args.args[0] == t("remind_time_added", "uk", time="09:30")


@pytest.mark.asyncio
async def test_remind_time_text_existing_and_invalid(storage):
    _shared.awaiting_remind_time[OWNER_ID] = True
    message = make_message(OWNER_ID, "21:00")
    await e.handle_financial_entry(message)
    assert message.answer.await_args.args[0] == t("remind_time_exists", "uk", time="21:00")

    _shared.awaiting_remind_time[OWNER_ID] = True
    message = make_message(OWNER_ID, "25:61")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("remind_time_invalid", "uk"))


@pytest.mark.asyncio
async def test_find_query_text_runs_search(storage, monkeypatch):
    run_find = AsyncMock()
    monkeypatch.setattr(e, "_run_find", run_find)
    _shared.awaiting_find_query[OWNER_ID] = True
    await e.handle_financial_entry(make_message(OWNER_ID, "  кафе  "))
    assert run_find.await_args.args[:2] == (OWNER_ID, "кафе")


@pytest.mark.asyncio
async def test_find_query_text_empty(storage, monkeypatch):
    run_find = AsyncMock()
    monkeypatch.setattr(e, "_run_find", run_find)
    _shared.awaiting_find_query[OWNER_ID] = True
    message = make_message(OWNER_ID, "   ")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("query_empty", "uk"))
    run_find.assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_category_text_then_amount_text_sets_the_limit(storage):
    _shared.awaiting_budget_category[OWNER_ID] = True
    message = make_message(OWNER_ID, "продукти")
    await e.handle_financial_entry(message)
    assert _shared.awaiting_budget_amount[OWNER_ID] == "Продукти"
    assert message.answer.await_args.args[0] == t("write_budget_amount", "uk", category="Продукти")

    message = make_message(OWNER_ID, "2500,5")
    await e.handle_financial_entry(message)
    storage["set_budget"].assert_awaited_once_with(OWNER_ID, "Продукти", 2500.5)
    assert message.answer.await_args.args[0] == t("budget_limit_set", "uk", category="Продукти", limit=2500.5)


@pytest.mark.asyncio
async def test_budget_category_text_empty(storage):
    _shared.awaiting_budget_category[OWNER_ID] = True
    message = make_message(OWNER_ID, "   ")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("category_empty", "uk"))
    assert OWNER_ID not in _shared.awaiting_budget_amount


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["abc", "0", "-1"])
async def test_budget_amount_text_rejects_bad_numbers(storage, text):
    _shared.awaiting_budget_amount[OWNER_ID] = "Кафе"
    message = make_message(OWNER_ID, text)
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("positive_number_prompt", "uk"))
    storage["set_budget"].assert_not_awaited()


@pytest.mark.asyncio
async def test_budget_amount_text_write_error(storage):
    storage["set_budget"].side_effect = RuntimeError("boom")
    _shared.awaiting_budget_amount[OWNER_ID] = "Кафе"
    message = make_message(OWNER_ID, "100")
    await e.handle_financial_entry(message)
    assert "boom" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_custom_category_text_updates_pending_entry(storage):
    entry_id = _pending_entry(description="кафе обід")
    _shared.awaiting_category_text[OWNER_ID] = entry_id
    message = make_message(OWNER_ID, "ресторан")

    await e.handle_financial_entry(message)

    assert _shared.pending_entries[entry_id]["category"] == "Ресторан"
    assert "Ресторан" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_custom_category_text_blank_keeps_category(storage):
    entry_id = _pending_entry()
    _shared.awaiting_category_text[OWNER_ID] = entry_id
    await e.handle_financial_entry(make_message(OWNER_ID, "   "))
    assert _shared.pending_entries[entry_id]["category"] == "Кафе"


@pytest.mark.asyncio
async def test_custom_category_text_for_expired_entry(storage):
    _shared.awaiting_category_text[OWNER_ID] = "gone"
    message = make_message(OWNER_ID, "ресторан")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("entry_expired", "uk"))


# ---- /edit field replies ----

@pytest.mark.asyncio
async def test_edit_amount_text_updates_the_sheet_row(storage):
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "amount")
    message = make_message(OWNER_ID, "175,5")

    await e.handle_financial_entry(message)

    storage["update_transaction_row"].assert_awaited_once_with(
        OWNER_ID, 5, "01.08.2026", "Expense", "Кафе", 175.5, "обід")
    assert _shared.pending_edits[edit_id]["amount"] == 175.5
    assert message.answer.await_args.args[0].startswith(t("edit_updated", "uk"))


@pytest.mark.asyncio
async def test_edit_category_text_is_normalized(storage):
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "category")
    await e.handle_financial_entry(make_message(OWNER_ID, "таксі"))
    assert storage["update_transaction_row"].await_args.args[4] == "Таксі"


@pytest.mark.asyncio
async def test_edit_description_text_blank_becomes_dash(storage):
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "description")
    await e.handle_financial_entry(make_message(OWNER_ID, "   "))
    assert storage["update_transaction_row"].await_args.args[6] == "-"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,text,key", [
    ("amount", "abc", "positive_number_prompt"),
    ("amount", "0", "positive_number_prompt"),
    ("category", "   ", "category_empty"),
])
async def test_invalid_edit_value_keeps_the_user_in_the_same_step(storage, field, text, key):
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, field)
    message = make_message(OWNER_ID, text)

    await e.handle_financial_entry(message)

    message.answer.assert_awaited_once_with(t(key, "uk"))
    assert _shared.awaiting_edit_field[OWNER_ID] == (edit_id, field)
    storage["update_transaction_row"].assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_text_for_expired_session(storage):
    _shared.awaiting_edit_field[OWNER_ID] = ("gone", "amount")
    message = make_message(OWNER_ID, "100")
    await e.handle_financial_entry(message)
    message.answer.assert_awaited_once_with(t("edit_expired", "uk"))


@pytest.mark.asyncio
async def test_edit_text_aborts_when_the_row_changed(storage):
    storage["get_transaction_row"].return_value = ["09.09.2026", "Expense", "Інше", 1, "x"]
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "amount")
    message = make_message(OWNER_ID, "100")

    await e.handle_financial_entry(message)

    message.answer.assert_awaited_once_with(t("edit_row_changed", "uk"))
    storage["update_transaction_row"].assert_not_awaited()  # must not overwrite the wrong row
    assert edit_id not in _shared.pending_edits


@pytest.mark.asyncio
async def test_edit_text_read_and_write_errors(storage):
    edit_id = _pending_edit()
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "amount")
    storage["get_transaction_row"].side_effect = RuntimeError("read boom")
    message = make_message(OWNER_ID, "100")
    await e.handle_financial_entry(message)
    assert "read boom" in message.answer.await_args.args[0]

    storage["get_transaction_row"].side_effect = None
    _shared.awaiting_edit_field[OWNER_ID] = (edit_id, "amount")
    storage["update_transaction_row"].side_effect = RuntimeError("write boom")
    message = make_message(OWNER_ID, "100")
    await e.handle_financial_entry(message)
    assert "write boom" in message.answer.await_args.args[0]
