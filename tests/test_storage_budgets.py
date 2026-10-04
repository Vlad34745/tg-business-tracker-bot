import pytest
from unittest.mock import Mock
from googleapiclient.errors import HttpError

import core.storage as storage
from core.storage import _client


def _http_error(status: int) -> HttpError:
    resp = Mock()
    resp.status = status
    return HttpError(resp, b"error body")


def _values(service):
    return service.spreadsheets.return_value.values.return_value


def _sheet(service):
    return service.spreadsheets.return_value


def _metadata(service, tabs: dict):
    """tabs: {tab_title: sheetId}"""
    _sheet(service).get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": t, "sheetId": i}} for t, i in tabs.items()]
    }


# --- get_budgets ---

@pytest.mark.asyncio
async def test_get_budgets_returns_rows(sheets_service):
    _values(sheets_service).get.return_value.execute.return_value = {
        "values": [["Кафе", 1000], ["Таксі", 500]]
    }
    assert await storage.get_budgets(111) == [["Кафе", 1000], ["Таксі", 500]]
    # owner (first allowed id) reads the plain "Budgets" tab, skipping the header
    assert _values(sheets_service).get.call_args.kwargs["range"] == "Budgets!A2:B"


@pytest.mark.asyncio
async def test_get_budgets_uses_suffixed_tab_for_other_users(sheets_service):
    _values(sheets_service).get.return_value.execute.return_value = {"values": []}
    await storage.get_budgets(222)
    assert _values(sheets_service).get.call_args.kwargs["range"] == "Budgets_222!A2:B"


@pytest.mark.asyncio
async def test_get_budgets_empty_when_no_values_key(sheets_service):
    _values(sheets_service).get.return_value.execute.return_value = {}
    assert await storage.get_budgets(111) == []


@pytest.mark.asyncio
async def test_get_budgets_empty_when_tab_missing(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(400)
    assert await storage.get_budgets(111) == []


@pytest.mark.asyncio
async def test_get_budgets_reraises_other_errors(sheets_service):
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.get_budgets(111)


# --- set_budget ---

@pytest.mark.asyncio
async def test_set_budget_updates_existing_category_case_insensitively(sheets_service):
    _metadata(sheets_service, {"Budgets": 1})
    _values(sheets_service).get.return_value.execute.return_value = {
        "values": [["Кафе"], ["Таксі"]]
    }

    await storage.set_budget(111, "таксі", 750)

    update_kwargs = _values(sheets_service).update.call_args.kwargs
    assert update_kwargs["range"] == "Budgets!A3:B3"  # 2nd data row -> sheet row 3
    assert update_kwargs["body"] == {"values": [["таксі", 750]]}
    assert _values(sheets_service).append.called is False


@pytest.mark.asyncio
async def test_set_budget_appends_new_category(sheets_service):
    _metadata(sheets_service, {"Budgets": 1})
    _values(sheets_service).get.return_value.execute.return_value = {"values": [["Кафе"]]}

    await storage.set_budget(111, "Продукти", 4000)

    append_kwargs = _values(sheets_service).append.call_args.kwargs
    assert append_kwargs["range"] == "Budgets!A:B"
    assert append_kwargs["body"] == {"values": [["Продукти", 4000]]}
    assert _values(sheets_service).update.called is False


@pytest.mark.asyncio
async def test_set_budget_creates_missing_tab_with_header(sheets_service):
    _metadata(sheets_service, {"Transactions": 0})  # no Budgets tab yet
    _values(sheets_service).get.return_value.execute.return_value = {}

    await storage.set_budget(111, "Кафе", 1000)

    add_sheet_body = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]
    assert add_sheet_body["requests"][0]["addSheet"]["properties"]["title"] == "Budgets"
    header_call = _values(sheets_service).update.call_args_list[0].kwargs
    assert header_call["range"] == "Budgets!A1:B1"
    assert header_call["body"] == {"values": [["Category", "MonthlyLimit"]]}


@pytest.mark.asyncio
async def test_set_budget_skips_metadata_check_for_known_tab(sheets_service):
    _client._known_existing_tabs.add("Budgets")
    _values(sheets_service).get.return_value.execute.return_value = {}

    await storage.set_budget(111, "Кафе", 1000)

    assert _sheet(sheets_service).get.called is False  # no metadata round trip


@pytest.mark.asyncio
async def test_set_budget_reraises_non_stale_errors(sheets_service):
    _client._known_existing_tabs.add("Budgets")
    _values(sheets_service).get.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.set_budget(111, "Кафе", 1000)


# --- delete_budget ---

@pytest.mark.asyncio
async def test_delete_budget_removes_matching_row(sheets_service):
    _metadata(sheets_service, {"Budgets": 42})
    _values(sheets_service).get.return_value.execute.return_value = {
        "values": [["Кафе"], ["Таксі"]]
    }

    assert await storage.delete_budget(111, "ТАКСІ") is True

    body = _sheet(sheets_service).batchUpdate.call_args.kwargs["body"]
    rng = body["requests"][0]["deleteDimension"]["range"]
    assert rng == {"sheetId": 42, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}


@pytest.mark.asyncio
async def test_delete_budget_false_when_category_not_found(sheets_service):
    _metadata(sheets_service, {"Budgets": 42})
    _values(sheets_service).get.return_value.execute.return_value = {"values": [["Кафе"]]}

    assert await storage.delete_budget(111, "Невідома") is False
    assert _sheet(sheets_service).batchUpdate.called is False


@pytest.mark.asyncio
async def test_delete_budget_false_when_tab_missing(sheets_service):
    _metadata(sheets_service, {"Transactions": 0})
    assert await storage.delete_budget(111, "Кафе") is False
    assert _values(sheets_service).get.called is False
