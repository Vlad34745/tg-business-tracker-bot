import pytest
from unittest.mock import AsyncMock

from core.handlers import export as h
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message

ROWS = [
    ["Date", "Type", "Category", "Amount", "Description"],
    ["01.08.2026", "Expense", "Кафе", 150, "обід"],
]


def _patch_rows(monkeypatch, rows=None, error=None):
    mock = AsyncMock(return_value=rows, side_effect=error)
    monkeypatch.setattr(h, "get_all_transactions", mock)
    return mock


# --- /export ---

@pytest.mark.asyncio
async def test_export_denied_for_stranger(monkeypatch):
    fetch = _patch_rows(monkeypatch, ROWS)
    message = make_message(STRANGER_ID, "/export")

    await h.cmd_export(message)

    message.answer.assert_awaited_once_with(t("access_denied", "uk"))
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_reports_sheet_read_error(monkeypatch):
    _patch_rows(monkeypatch, error=RuntimeError("boom"))
    message = make_message(OWNER_ID, "/export")

    await h.cmd_export(message)

    assert "boom" in message.answer.await_args.args[0]
    message.answer_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_with_no_entries(monkeypatch):
    _patch_rows(monkeypatch, [])
    message = make_message(OWNER_ID, "/export")

    await h.cmd_export(message)

    message.answer.assert_awaited_once_with(t("no_entries_yet", "uk"))
    message.answer_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_export_sends_csv_file_with_bom(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    message = make_message(OWNER_ID, "/export")

    await h.cmd_export(message)

    document = message.answer_document.await_args.args[0]
    assert document.filename.startswith("transactions_") and document.filename.endswith(".csv")
    assert document.data.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM so Excel reads Cyrillic
    assert "Кафе" in document.data.decode("utf-8-sig")
    assert message.answer_document.await_args.kwargs["caption"] == t("export_caption", "uk", n=2)


# --- nav:export button ---

@pytest.mark.asyncio
async def test_nav_export_denied_for_stranger(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    callback = make_callback(STRANGER_ID, "nav:export")

    await h.cb_nav_export(callback)

    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.answer_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_nav_export_reports_sheet_read_error(monkeypatch):
    _patch_rows(monkeypatch, error=RuntimeError("boom"))
    callback = make_callback(OWNER_ID, "nav:export")

    await h.cb_nav_export(callback)

    assert "boom" in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_nav_export_with_no_entries(monkeypatch):
    _patch_rows(monkeypatch, [])
    callback = make_callback(OWNER_ID, "nav:export")

    await h.cb_nav_export(callback)

    callback.message.answer.assert_awaited_once_with(t("no_entries_yet", "uk"))


@pytest.mark.asyncio
async def test_nav_export_sends_csv_file(monkeypatch):
    _patch_rows(monkeypatch, ROWS)
    callback = make_callback(OWNER_ID, "nav:export")

    await h.cb_nav_export(callback)

    document = callback.message.answer_document.await_args.args[0]
    assert document.filename.endswith(".csv")
    assert callback.message.answer_document.await_args.kwargs["caption"] == t("export_caption", "uk", n=2)
