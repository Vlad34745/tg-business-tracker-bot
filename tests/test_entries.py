import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.handlers._shared import _raw_rows_equal
from core.handlers import entries as e


# --- _raw_rows_equal ---

def test_raw_rows_equal_identical():
    row = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    assert _raw_rows_equal(row, list(row)) is True


def test_raw_rows_equal_tolerates_float_vs_int_amount():
    a = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    b = ["01.08.2026", "Expense", "Кафе", 150.0, "обід"]
    assert _raw_rows_equal(a, b) is True


def test_raw_rows_equal_false_for_different_rows():
    a = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    b = ["02.08.2026", "Expense", "Таксі", 75, "B"]
    assert _raw_rows_equal(a, b) is False


def test_raw_rows_equal_false_when_either_is_none():
    row = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    assert _raw_rows_equal(row, None) is False
    assert _raw_rows_equal(None, row) is False
    assert _raw_rows_equal(None, None) is False


def test_raw_rows_equal_false_on_amount_difference():
    a = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    b = ["01.08.2026", "Expense", "Кафе", 151, "обід"]
    assert _raw_rows_equal(a, b) is False


# --- /undo stale-row guard (cb_undo_confirm) ---

@pytest.fixture(autouse=True)
def clean_undo_snapshot():
    yield
    e._undo_snapshot.pop(123, None)


@pytest.mark.asyncio
async def test_undo_confirm_aborts_when_row_changed(monkeypatch):
    # Simulates: /undo showed one row, but before the person tapped
    # confirm, the sheet changed (e.g. a new entry was appended) so
    # "the last row" now means something different.
    e._undo_snapshot[123] = {"row": ["01.08.2026", "Expense", "Кафе", 150, "обід"]}

    monkeypatch.setattr(e, "get_last_transaction", AsyncMock(
        return_value=["02.08.2026", "Expense", "Нова покупка", 999, "-"]
    ))
    delete_mock = AsyncMock()
    monkeypatch.setattr(e, "delete_last_transaction", delete_mock)

    callback = MagicMock()
    callback.from_user.id = 123
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    with patch("core.handlers.entries.language.get_language", return_value="uk"), \
         patch("core.handlers.entries.is_owner", return_value=True):
        await e.cb_undo_confirm(callback)

    assert delete_mock.called is False  # must NOT delete when the row changed
    shown_text = callback.message.edit_text.call_args[0][0]
    assert "змінилися" in shown_text  # the warning message, not a success message
    assert 123 not in e._undo_snapshot  # snapshot consumed either way


@pytest.mark.asyncio
async def test_undo_confirm_deletes_when_row_unchanged(monkeypatch):
    matching_row = ["01.08.2026", "Expense", "Кафе", 150, "обід"]
    e._undo_snapshot[123] = {"row": matching_row}

    monkeypatch.setattr(e, "get_last_transaction", AsyncMock(return_value=list(matching_row)))
    delete_mock = AsyncMock(return_value=matching_row)
    monkeypatch.setattr(e, "delete_last_transaction", delete_mock)

    callback = MagicMock()
    callback.from_user.id = 123
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    with patch("core.handlers.entries.language.get_language", return_value="uk"), \
         patch("core.handlers.entries.is_owner", return_value=True):
        await e.cb_undo_confirm(callback)

    assert delete_mock.called is True  # happy path: row unchanged, delete proceeds


@pytest.mark.asyncio
async def test_undo_batch_confirm_aborts_when_rows_changed(monkeypatch):
    e._undo_snapshot[123] = {"rows": [
        ["01.08.2026", "Expense", "Кафе", 100, "-"],
        ["01.08.2026", "Expense", "Таксі", 50, "-"],
    ]}

    # current last-2 rows no longer match the snapshot
    monkeypatch.setattr(e, "get_last_n_transactions", AsyncMock(return_value=[
        ["02.08.2026", "Expense", "Щось інше", 999, "-"],
        ["02.08.2026", "Expense", "Ще щось", 888, "-"],
    ]))
    delete_mock = AsyncMock()
    monkeypatch.setattr(e, "delete_last_n_transactions", delete_mock)

    callback = MagicMock()
    callback.from_user.id = 123
    callback.data = "undo_batch_confirm:2"
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    with patch("core.handlers.entries.language.get_language", return_value="uk"), \
         patch("core.handlers.entries.is_owner", return_value=True):
        await e.cb_undo_batch_confirm(callback)

    assert delete_mock.called is False
