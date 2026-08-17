import pytest
from unittest.mock import Mock

from core import sheets
from googleapiclient.errors import HttpError


@pytest.fixture(autouse=True)
def isolate_allowed_ids(monkeypatch):
    """Every test gets a clean, explicit ALLOWED_IDS list rather than
    whatever happens to be in the real environment's .env file."""
    monkeypatch.setattr(sheets, "ALLOWED_IDS", ["111", "222"])
    yield


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """_retry_call uses exponential backoff via time.sleep — patch it
    out so retry tests run instantly instead of taking several seconds."""
    monkeypatch.setattr(sheets.time, "sleep", lambda seconds: None)


def _http_error(status: int) -> HttpError:
    resp = Mock()
    resp.status = status
    return HttpError(resp, b"error body")


# --- _tab_name ---

def test_tab_name_owner_gets_plain_name():
    assert sheets._tab_name("Transactions", 111) == "Transactions"
    assert sheets._tab_name("Budgets", 111) == "Budgets"


def test_tab_name_other_user_gets_suffixed_name():
    assert sheets._tab_name("Transactions", 222) == "Transactions_222"
    assert sheets._tab_name("Transactions", 999) == "Transactions_999"


def test_tab_name_no_allowed_ids_configured(monkeypatch):
    monkeypatch.setattr(sheets, "ALLOWED_IDS", [])
    # With no configured owner, nobody gets the plain, un-suffixed tab.
    assert sheets._tab_name("Transactions", 111) == "Transactions_111"


def test_tab_name_different_users_get_different_tabs():
    assert sheets._tab_name("Transactions", 222) != sheets._tab_name("Transactions", 333)


# --- _is_transient_error ---

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_is_transient_error_true_for_known_transient_statuses(status):
    assert sheets._is_transient_error(_http_error(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_is_transient_error_false_for_client_errors(status):
    assert sheets._is_transient_error(_http_error(status)) is False


def test_is_transient_error_true_for_connection_error():
    assert sheets._is_transient_error(ConnectionError("network blip")) is True


def test_is_transient_error_true_for_timeout_error():
    assert sheets._is_transient_error(TimeoutError("timed out")) is True


def test_is_transient_error_false_for_generic_exception():
    assert sheets._is_transient_error(ValueError("not a transient issue")) is False


# --- _retry_call ---

def test_retry_call_returns_result_on_first_success():
    assert sheets._retry_call(lambda: "ok") == "ok"


def test_retry_call_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return "recovered"

    assert sheets._retry_call(flaky) == "recovered"
    assert calls["n"] == 3


def test_retry_call_raises_immediately_on_non_transient_error():
    calls = {"n": 0}

    def bad_request():
        calls["n"] += 1
        raise _http_error(400)

    with pytest.raises(HttpError):
        sheets._retry_call(bad_request)
    assert calls["n"] == 1  # no retries attempted


def test_retry_call_raises_after_exhausting_retries():
    calls = {"n": 0}

    def always_flaky():
        calls["n"] += 1
        raise _http_error(503)

    with pytest.raises(HttpError):
        sheets._retry_call(always_flaky)
    assert calls["n"] == sheets._MAX_RETRIES


# --- _get_sheets_service caching ---

def test_get_sheets_service_is_cached(monkeypatch):
    monkeypatch.setattr(sheets, "_service_cache", None)
    monkeypatch.setattr(sheets.os.path, "exists", lambda path: True)

    build_calls = {"n": 0}

    def fake_build(*args, **kwargs):
        build_calls["n"] += 1
        return Mock()

    monkeypatch.setattr(sheets, "build", fake_build)
    monkeypatch.setattr(sheets.Credentials, "from_service_account_file", lambda *a, **k: Mock())

    first = sheets._get_sheets_service()
    second = sheets._get_sheets_service()

    assert first is second
    assert build_calls["n"] == 1  # only built once, second call used the cache


def test_get_sheets_service_raises_if_credentials_missing(monkeypatch):
    monkeypatch.setattr(sheets, "_service_cache", None)
    monkeypatch.setattr(sheets.os.path, "exists", lambda path: False)

    with pytest.raises(FileNotFoundError):
        sheets._get_sheets_service()


# --- get_recent_transactions_with_index / update_transaction_row / delete_transaction_row ---

@pytest.fixture
def mock_sheets_service(monkeypatch):
    """A MagicMock standing in for the built Google Sheets service,
    installed as the module's cached service so no real API call or
    credentials file is needed."""
    from unittest.mock import MagicMock
    mock_service = MagicMock()
    monkeypatch.setattr(sheets, "_service_cache", mock_service)
    return mock_service


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_maps_rows_correctly(mock_sheets_service):
    fake_rows = [
        ["Date", "Type", "Category", "Amount", "Description"],  # header = sheet row 0
        ["01.08.2026", "Expense", "Кафе", 100, "A"],              # sheet row 1
        ["02.08.2026", "Expense", "Таксі", 50, "B"],              # sheet row 2
        ["03.08.2026", "Income", "Зарплата", 20000, "C"],         # sheet row 3
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    result = await sheets.get_recent_transactions_with_index(111, n=10)

    assert result == [
        (1, ["01.08.2026", "Expense", "Кафе", 100, "A"]),
        (2, ["02.08.2026", "Expense", "Таксі", 50, "B"]),
        (3, ["03.08.2026", "Income", "Зарплата", 20000, "C"]),
    ]


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_respects_n(mock_sheets_service):
    fake_rows = [["Date", "Type", "Category", "Amount", "Description"]] + [
        [f"0{i}.08.2026", "Expense", "Cat", i * 10, "-"] for i in range(1, 6)
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    result = await sheets.get_recent_transactions_with_index(111, n=2)
    assert len(result) == 2
    assert result[-1][1][0] == "05.08.2026"  # most recent last


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_empty_when_no_data(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [["Date", "Type", "Category", "Amount", "Description"]]  # header only
    }
    result = await sheets.get_recent_transactions_with_index(111, n=10)
    assert result == []


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_empty_when_tab_missing(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = _http_error(400)
    result = await sheets.get_recent_transactions_with_index(111, n=10)
    assert result == []


@pytest.mark.asyncio
async def test_update_transaction_row_uses_correct_a1_range(mock_sheets_service):
    # 0-based sheet row index 2 (third data row after the header) must
    # map to A1 notation row 3 (index + 1).
    await sheets.update_transaction_row(111, 2, "02.08.2026", "Expense", "Таксі", 75, "B updated")

    call = mock_sheets_service.spreadsheets.return_value.values.return_value.update.call_args
    assert call.kwargs["range"] == "Transactions!A3:E3"
    assert call.kwargs["body"] == {"values": [["02.08.2026", "Expense", "Таксі", 75, "B updated"]]}


@pytest.mark.asyncio
async def test_delete_transaction_row_targets_correct_index(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Transactions", "sheetId": 999}}]
    }

    result = await sheets.delete_transaction_row(111, 2)

    assert result is True
    call = mock_sheets_service.spreadsheets.return_value.batchUpdate.call_args
    delete_range = call.kwargs["body"]["requests"][0]["deleteDimension"]["range"]
    assert delete_range == {"sheetId": 999, "dimension": "ROWS", "startIndex": 2, "endIndex": 3}


@pytest.mark.asyncio
async def test_delete_transaction_row_returns_false_when_tab_missing(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}
    result = await sheets.delete_transaction_row(111, 2)
    assert result is False
