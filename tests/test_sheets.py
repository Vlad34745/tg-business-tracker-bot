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
    # _known_existing_tabs is a process-lifetime cache in real usage,
    # but tests must not leak state into each other — a tab confirmed
    # "existing" in one test must not silently skip the metadata check
    # in the next test's assertions.
    monkeypatch.setattr(sheets, "_known_existing_tabs", set())
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

    page, has_more = await sheets.get_recent_transactions_with_index(111, n=10)

    assert page == [
        (1, ["01.08.2026", "Expense", "Кафе", 100, "A"]),
        (2, ["02.08.2026", "Expense", "Таксі", 50, "B"]),
        (3, ["03.08.2026", "Income", "Зарплата", 20000, "C"]),
    ]
    assert has_more is False  # fewer rows than the page size — nothing older


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_respects_n(mock_sheets_service):
    fake_rows = [["Date", "Type", "Category", "Amount", "Description"]] + [
        [f"0{i}.08.2026", "Expense", "Cat", i * 10, "-"] for i in range(1, 6)
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    page, has_more = await sheets.get_recent_transactions_with_index(111, n=2)
    assert len(page) == 2
    assert page[-1][1][0] == "05.08.2026"  # most recent last
    assert has_more is True  # 5 rows total, only showed the last 2


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_pagination_offset(mock_sheets_service):
    # 5 data rows; page size 2, offset 2 should skip the 2 most recent
    # and return the 2 before those, with has_more still True (1 row
    # older than this page remains).
    fake_rows = [["Date", "Type", "Category", "Amount", "Description"]] + [
        [f"0{i}.08.2026", "Expense", "Cat", i * 10, "-"] for i in range(1, 6)
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    page, has_more = await sheets.get_recent_transactions_with_index(111, n=2, offset=2)
    assert [row[0] for _idx, row in page] == ["02.08.2026", "03.08.2026"]
    assert has_more is True


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_last_page_has_no_more(mock_sheets_service):
    fake_rows = [["Date", "Type", "Category", "Amount", "Description"]] + [
        [f"0{i}.08.2026", "Expense", "Cat", i * 10, "-"] for i in range(1, 6)
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    # offset=4 skips the 4 most recent, leaving only the single oldest row.
    page, has_more = await sheets.get_recent_transactions_with_index(111, n=2, offset=4)
    assert [row[0] for _idx, row in page] == ["01.08.2026"]
    assert has_more is False


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_empty_when_no_data(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [["Date", "Type", "Category", "Amount", "Description"]]  # header only
    }
    page, has_more = await sheets.get_recent_transactions_with_index(111, n=10)
    assert page == []
    assert has_more is False


@pytest.mark.asyncio
async def test_get_recent_transactions_with_index_empty_when_tab_missing(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = _http_error(400)
    page, has_more = await sheets.get_recent_transactions_with_index(111, n=10)
    assert page == []
    assert has_more is False


@pytest.mark.asyncio
async def test_get_all_transactions_with_index_maps_rows_correctly(mock_sheets_service):
    fake_rows = [
        ["Date", "Type", "Category", "Amount", "Description"],
        ["01.08.2026", "Expense", "Кафе", 100, "A"],
        ["02.08.2026", "Expense", "Таксі", 50, "B"],
    ]
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": fake_rows
    }

    result = await sheets.get_all_transactions_with_index(111)
    assert result == [
        (1, ["01.08.2026", "Expense", "Кафе", 100, "A"]),
        (2, ["02.08.2026", "Expense", "Таксі", 50, "B"]),
    ]


@pytest.mark.asyncio
async def test_get_all_transactions_with_index_empty_when_tab_missing(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = _http_error(400)
    result = await sheets.get_all_transactions_with_index(111)
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


# --- append_transactions_batch ---

@pytest.mark.asyncio
async def test_append_transactions_batch_makes_a_single_api_call(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Transactions", "sheetId": 1}}]
    }
    entries = [
        {"date": "01.08.2026", "type_tr": "Expense", "category": "Кафе", "amount": 100, "description": "-"},
        {"date": "01.08.2026", "type_tr": "Expense", "category": "Таксі", "amount": 50, "description": "-"},
        {"date": "01.08.2026", "type_tr": "Income", "category": "Зарплата", "amount": 20000, "description": "-"},
    ]

    await sheets.append_transactions_batch(111, entries)

    append_calls = mock_sheets_service.spreadsheets.return_value.values.return_value.append.call_args_list
    assert len(append_calls) == 1  # one API call, not one per entry
    assert append_calls[0].kwargs["body"]["values"] == [
        ["01.08.2026", "Expense", "Кафе", 100, "-"],
        ["01.08.2026", "Expense", "Таксі", 50, "-"],
        ["01.08.2026", "Income", "Зарплата", 20000, "-"],
    ]


@pytest.mark.asyncio
async def test_append_transactions_batch_creates_tab_if_missing(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}
    entries = [{"date": "01.08.2026", "type_tr": "Expense", "category": "Кафе", "amount": 100, "description": "-"}]

    await sheets.append_transactions_batch(111, entries)

    create_call = mock_sheets_service.spreadsheets.return_value.batchUpdate.call_args
    assert create_call.kwargs["body"]["requests"][0]["addSheet"]["properties"]["title"] == "Transactions"


# --- _known_existing_tabs cache ---

@pytest.mark.asyncio
async def test_tab_exists_metadata_checked_once_across_multiple_appends(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [{"properties": {"title": "Transactions", "sheetId": 1}}]
    }

    for i in range(5):
        await sheets.append_transaction(111, f"0{i + 1}.08.2026", "Expense", "Cat", 10, "-")

    metadata_calls = mock_sheets_service.spreadsheets.return_value.get.call_args_list
    assert len(metadata_calls) == 1  # cached after the first call, not re-checked for the other 4


@pytest.mark.asyncio
async def test_tab_exists_cache_is_per_tab_name(mock_sheets_service):
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"title": "Transactions", "sheetId": 1}},
            {"properties": {"title": "Transactions_222", "sheetId": 2}},
        ]
    }

    await sheets.append_transaction(111, "01.08.2026", "Expense", "Cat", 10, "-")  # owner -> "Transactions"
    await sheets.append_transaction(222, "01.08.2026", "Expense", "Cat", 10, "-")  # other user -> "Transactions_222"

    metadata_calls = mock_sheets_service.spreadsheets.return_value.get.call_args_list
    assert len(metadata_calls) == 2  # different tab names, each checked once


# --- self-heal when a tab was deleted by hand (cache says it exists, API disagrees) ---

@pytest.mark.asyncio
async def test_append_transaction_self_heals_after_manual_tab_deletion(mock_sheets_service):
    sheets._known_existing_tabs.add("Transactions")  # cache believes the tab still exists

    call_count = {"n": 0}

    def fake_append(**kwargs):
        call_count["n"] += 1
        result = Mock()
        if call_count["n"] == 1:
            result.execute.side_effect = _http_error(400)
        else:
            result.execute.return_value = {"updates": {"updatedRows": 1}}
        return result

    mock_sheets_service.spreadsheets.return_value.values.return_value.append.side_effect = fake_append
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}

    await sheets.append_transaction(111, "01.08.2026", "Expense", "Кафе", 100, "-")

    assert call_count["n"] == 2  # first attempt failed, retried once after recreating the tab
    assert mock_sheets_service.spreadsheets.return_value.batchUpdate.called  # tab was recreated
    assert "Transactions" in sheets._known_existing_tabs  # cache repopulated after self-heal


@pytest.mark.asyncio
async def test_append_transaction_reraises_non_stale_400_errors(mock_sheets_service):
    # A 400 for a tab NOT in the cache is a normal "doesn't exist yet"
    # case already handled by _ensure_transactions_sheet_exists — this
    # self-heal path should only trigger for a tab the cache actively
    # believed existed. Simulate a 400 that isn't the stale-cache case
    # by not adding the tab to _known_existing_tabs first.
    mock_sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = _http_error(400)
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}

    with pytest.raises(HttpError):
        await sheets.append_transaction(111, "01.08.2026", "Expense", "Кафе", 100, "-")


@pytest.mark.asyncio
async def test_append_transactions_batch_self_heals_after_manual_tab_deletion(mock_sheets_service):
    sheets._known_existing_tabs.add("Transactions")

    call_count = {"n": 0}

    def fake_append(**kwargs):
        call_count["n"] += 1
        result = Mock()
        if call_count["n"] == 1:
            result.execute.side_effect = _http_error(400)
        else:
            result.execute.return_value = {"updates": {"updatedRows": 2}}
        return result

    mock_sheets_service.spreadsheets.return_value.values.return_value.append.side_effect = fake_append
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}

    entries = [
        {"date": "01.08.2026", "type_tr": "Expense", "category": "Кафе", "amount": 100, "description": "-"},
        {"date": "01.08.2026", "type_tr": "Expense", "category": "Таксі", "amount": 50, "description": "-"},
    ]
    await sheets.append_transactions_batch(111, entries)

    assert call_count["n"] == 2
    assert "Transactions" in sheets._known_existing_tabs


@pytest.mark.asyncio
async def test_set_budget_self_heals_after_manual_tab_deletion(mock_sheets_service):
    sheets._known_existing_tabs.add("Budgets")

    get_values_call_count = {"n": 0}

    def fake_get(**kwargs):
        get_values_call_count["n"] += 1
        result = Mock()
        if get_values_call_count["n"] == 1:
            result.execute.side_effect = _http_error(400)
        else:
            result.execute.return_value = {"values": []}
        return result

    mock_sheets_service.spreadsheets.return_value.values.return_value.get.side_effect = fake_get
    mock_sheets_service.spreadsheets.return_value.get.return_value.execute.return_value = {"sheets": []}

    await sheets.set_budget(111, "Кафе", 1000)

    assert get_values_call_count["n"] == 2  # first (failed) lookup, then the retried one
    assert "Budgets" in sheets._known_existing_tabs
    # the retried write should have gone through as an append (new category)
    assert mock_sheets_service.spreadsheets.return_value.values.return_value.append.called