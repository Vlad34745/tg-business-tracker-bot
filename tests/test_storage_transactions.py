import pytest
from unittest.mock import Mock
from googleapiclient.errors import HttpError

import core.storage as storage
from core.storage import _client

HEADER = ["Date", "Type", "Category", "Amount", "Description"]
ROW_A = ["01.08.2026", "Expense", "Кафе", 100, "A"]
ROW_B = ["02.08.2026", "Expense", "Таксі", 50, "B"]
ROW_C = ["03.08.2026", "Income", "Зарплата", 20000, "C"]


def _http_error(status: int) -> HttpError:
    resp = Mock()
    resp.status = status
    return HttpError(resp, b"error body")


def _values(service):
    return service.spreadsheets.return_value.values.return_value


def _sheet(service):
    return service.spreadsheets.return_value


def _set_rows(service, rows):
    _values(service).get.return_value.execute.return_value = {"values": rows}


def _set_tabs(service, tabs: dict):
    _sheet(service).get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": t, "sheetId": i}} for t, i in tabs.items()]
    }


# --- append_transaction ---

@pytest.mark.asyncio
async def test_append_transaction_writes_one_row(sheets_service):
    _client._known_existing_tabs.add("Transactions")

    await storage.append_transaction(111, "01.08.2026", "Expense", "Кафе", 150, "обід")

    kwargs = _values(sheets_service).append.call_args.kwargs
    assert kwargs["range"] == "Transactions!A:E"
    assert kwargs["body"] == {"values": [["01.08.2026", "Expense", "Кафе", 150, "обід"]]}


@pytest.mark.asyncio
async def test_append_transaction_creates_tab_for_new_user(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 0})  # user 222 has no tab yet

    await storage.append_transaction(222, "01.08.2026", "Expense", "Кафе", 150, "обід")

    created = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]
    assert created["requests"][0]["addSheet"]["properties"]["title"] == "Transactions_222"
    header = _values(sheets_service).update.call_args.kwargs
    assert header["range"] == "Transactions_222!A1:E1"
    assert header["body"] == {"values": [HEADER]}


# --- get_last_transaction ---

@pytest.mark.asyncio
async def test_get_last_transaction_returns_last_row(sheets_service):
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B])
    assert await storage.get_last_transaction(111) == ROW_B


@pytest.mark.asyncio
async def test_get_last_transaction_none_when_no_rows(sheets_service):
    _set_rows(sheets_service, [])
    assert await storage.get_last_transaction(111) is None


@pytest.mark.asyncio
async def test_get_last_transaction_none_when_tab_missing(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(400)
    assert await storage.get_last_transaction(111) is None


@pytest.mark.asyncio
async def test_get_last_transaction_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_last_transaction(111)


# --- delete_last_transaction ---

@pytest.mark.asyncio
async def test_delete_last_transaction_deletes_and_returns_last_row(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 7})
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B])

    assert await storage.delete_last_transaction(111) == ROW_B

    body = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]
    rng = body["requests"][0]["deleteDimension"]["range"]
    assert rng == {"sheetId": 7, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}


@pytest.mark.asyncio
async def test_delete_last_transaction_none_when_tab_missing(sheets_service):
    _set_tabs(sheets_service, {"Budgets": 1})
    assert await storage.delete_last_transaction(111) is None
    assert _sheet(sheets_service).batchUpdate.called is False


@pytest.mark.asyncio
async def test_delete_last_transaction_none_when_tab_empty(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 7})
    _set_rows(sheets_service, [])
    assert await storage.delete_last_transaction(111) is None
    assert _sheet(sheets_service).batchUpdate.called is False


# --- get_last_n_transactions ---

@pytest.mark.asyncio
async def test_get_last_n_transactions_returns_last_n(sheets_service):
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B, ROW_C])
    assert await storage.get_last_n_transactions(111, 2) == [ROW_B, ROW_C]


@pytest.mark.asyncio
async def test_get_last_n_transactions_fewer_rows_than_n(sheets_service):
    _set_rows(sheets_service, [ROW_A])
    assert await storage.get_last_n_transactions(111, 5) == [ROW_A]


@pytest.mark.asyncio
async def test_get_last_n_transactions_empty(sheets_service):
    _set_rows(sheets_service, [])
    assert await storage.get_last_n_transactions(111, 3) == []


@pytest.mark.asyncio
async def test_get_last_n_transactions_empty_when_tab_missing(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(400)
    assert await storage.get_last_n_transactions(111, 3) == []


@pytest.mark.asyncio
async def test_get_last_n_transactions_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_last_n_transactions(111, 3)


# --- delete_last_n_transactions ---

@pytest.mark.asyncio
async def test_delete_last_n_transactions_deletes_range(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 7})
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B, ROW_C])

    assert await storage.delete_last_n_transactions(111, 2) == [ROW_B, ROW_C]

    body = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]
    rng = body["requests"][0]["deleteDimension"]["range"]
    assert rng == {"sheetId": 7, "dimension": "ROWS", "startIndex": 2, "endIndex": 4}


@pytest.mark.asyncio
async def test_delete_last_n_transactions_caps_at_available_rows(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 7})
    _set_rows(sheets_service, [ROW_A, ROW_B])

    assert await storage.delete_last_n_transactions(111, 10) == [ROW_A, ROW_B]

    rng = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]["requests"][0]["deleteDimension"]["range"]
    assert (rng["startIndex"], rng["endIndex"]) == (0, 2)


@pytest.mark.asyncio
async def test_delete_last_n_transactions_empty_when_tab_missing(sheets_service):
    _set_tabs(sheets_service, {})
    assert await storage.delete_last_n_transactions(111, 2) == []


@pytest.mark.asyncio
async def test_delete_last_n_transactions_empty_when_no_rows(sheets_service):
    _set_tabs(sheets_service, {"Transactions": 7})
    _set_rows(sheets_service, [])
    assert await storage.delete_last_n_transactions(111, 2) == []
    assert _sheet(sheets_service).batchUpdate.called is False


# --- get_all_transactions ---

@pytest.mark.asyncio
async def test_get_all_transactions_returns_every_row(sheets_service):
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B])
    assert await storage.get_all_transactions(111) == [HEADER, ROW_A, ROW_B]


@pytest.mark.asyncio
async def test_get_all_transactions_empty_when_tab_missing(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(400)
    assert await storage.get_all_transactions(111) == []


@pytest.mark.asyncio
async def test_get_all_transactions_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_all_transactions(111)


# --- get_all_transactions_with_index ---

@pytest.mark.asyncio
async def test_get_all_transactions_with_index_header_only_is_empty(sheets_service):
    _set_rows(sheets_service, [HEADER])
    assert await storage.get_all_transactions_with_index(111) == []


@pytest.mark.asyncio
async def test_get_all_transactions_with_index_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_all_transactions_with_index(111)


# --- get_recent_transactions_with_index ---

@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_recent_transactions_with_index(111)


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_offset_past_end_is_empty(sheets_service):
    _set_rows(sheets_service, [HEADER, ROW_A, ROW_B])
    page, has_more = await storage.get_recent_transactions_with_index(111, n=10, offset=5)
    assert page == []
    assert has_more is False


# --- get_transaction_row ---

@pytest.mark.asyncio
async def test_get_transaction_row_returns_row_and_uses_a1_range(sheets_service):
    _set_rows(sheets_service, [ROW_B])
    assert await storage.get_transaction_row(111, 2) == ROW_B
    assert _values(sheets_service).get.call_args.kwargs["range"] == "Transactions!A3:E3"


@pytest.mark.asyncio
async def test_get_transaction_row_none_when_row_gone(sheets_service):
    _set_rows(sheets_service, [])
    assert await storage.get_transaction_row(111, 99) is None


@pytest.mark.asyncio
async def test_get_transaction_row_none_when_tab_missing(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(400)
    assert await storage.get_transaction_row(111, 1) is None


@pytest.mark.asyncio
async def test_get_transaction_row_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_transaction_row(111, 1)
