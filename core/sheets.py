import os
import time
import random
import asyncio
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()

# Path to the Google Service Account credentials file
CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# API access scope for Google Sheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Retry policy for transient Google Sheets API failures — rate limits
# and momentary server/network hiccups shouldn't surface as an error
# to the user if a short retry would have succeeded. Non-transient
# errors (bad request, auth failure) are never retried. Note this
# retries the *entire* sync_worker, including any writes inside it —
# if a write actually succeeded server-side but the response was lost
# before we saw it, a retry could in rare cases duplicate that write.
# This is an accepted trade-off: silently failing on a blip is worse
# for a single-user finance tracker than a rare duplicate row, which
# is easy to spot and fix with /undo.
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        return exc.resp.status in _TRANSIENT_STATUS_CODES
    return isinstance(exc, (ConnectionError, TimeoutError))


def _retry_call(fn):
    """
    Runs a synchronous Sheets API call with exponential backoff (plus
    jitter) on transient errors. Raises immediately for non-transient
    errors or once retries are exhausted.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as e:
            if not _is_transient_error(e) or attempt == _MAX_RETRIES - 1:
                raise
            last_exc = e
            delay = _BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(delay)
    raise last_exc  # pragma: no cover - loop above always returns or raises

# Per-user tab isolation: each allowed user gets their own
# "Transactions_<id>" / "Budgets_<id>" tab so multiple people sharing
# the bot don't see or accidentally delete each other's entries. The
# first ID in ALLOWED_USER_ID is treated as the original owner and
# keeps using the plain "Transactions"/"Budgets" tab names, so data
# already in the sheet from before multi-user support stays right
# where it is — no manual migration needed.
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_ID", "")
ALLOWED_IDS = [str(uid).strip() for uid in ALLOWED_IDS_RAW.split(",") if uid.strip()]


def _tab_name(base: str, user_id: int) -> str:
    if ALLOWED_IDS and str(user_id) == ALLOWED_IDS[0]:
        return base
    return f"{base}_{user_id}"


_service_cache = None


def _get_sheets_service():
    """
    Internal synchronous helper to initialize the Google Sheets API
    client. The built service object is cached at module level — the
    credentials file only needs to be read and the client only needs
    to be constructed once per process, not on every single API call.
    """
    global _service_cache
    if _service_cache is not None:
        return _service_cache

    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(f"Google credentials file missing at: {CREDENTIALS_PATH}")

    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    _service_cache = build("sheets", "v4", credentials=creds)
    return _service_cache


def _tab_exists(sheet, tab_name: str) -> bool:
    metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [tab["properties"]["title"] for tab in metadata.get("sheets", [])]
    return tab_name in titles


def _get_sheet_id(sheet, tab_name: str):
    """Returns the numeric sheetId for a tab, or None if it doesn't exist."""
    metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    for tab in metadata.get("sheets", []):
        if tab["properties"]["title"] == tab_name:
            return tab["properties"]["sheetId"]
    return None


def _ensure_transactions_sheet_exists(sheet, tab_name: str):
    """
    Creates a transactions tab with a header row if it doesn't exist
    yet. Called lazily from append_transaction — a brand new user's
    tab isn't created until their first saved entry.
    """
    if _tab_exists(sheet, tab_name):
        return

    sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "requests": [{"addSheet": {"properties": {"title": tab_name}}}]
    }).execute()
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A1:E1",
        valueInputOption="USER_ENTERED",
        body={"values": [["Date", "Type", "Category", "Amount", "Description"]]}
    ).execute()


async def append_transaction(user_id: int, date: str, type_tr: str, category: str, amount: float, description: str):
    """
    Asynchronously appends a transaction row into this user's
    Transactions tab (creating the tab on first use).
    Uses asyncio.to_thread to prevent API requests from blocking the bot's main loop.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)
        _ensure_transactions_sheet_exists(sheet, tab_name)

        row_values = [[date, type_tr, category, amount, description]]
        body = {"values": row_values}
        range_name = f"{tab_name}!A:E"

        request = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        )
        return request.execute()

    # Offload the synchronous API call to a separate background thread
    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_last_transaction(user_id: int):
    """
    Asynchronously fetches the most recently added transaction row
    from this user's Transactions tab.

    Returns:
        A list [date, type_tr, category, amount, description] for the
        last row, or None if the tab has no data rows yet (or doesn't
        exist yet because this user hasn't saved anything).
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A:E",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"
            ).execute()
            return result.get("values", [])
        except HttpError as e:
            if e.resp.status == 400:
                return []  # tab doesn't exist yet — no entries for this user
            raise

    rows = await asyncio.to_thread(_retry_call, sync_worker)
    if not rows:
        return None

    return rows[-1]


async def delete_last_transaction(user_id: int):
    """
    Asynchronously deletes the last row from this user's Transactions
    tab.

    Returns:
        The deleted row's data as a list, or None if the tab had no
        data rows to delete.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        sheet_id = _get_sheet_id(sheet, tab_name)
        if sheet_id is None:
            return None  # no tab yet — nothing to delete

        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{tab_name}!A:E",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING"
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return None

        last_row_data = rows[-1]
        # 0-based row index within the sheet — matches the row's
        # position since the range starts at row 1.
        last_row_index = len(rows) - 1

        delete_request = {
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": last_row_index,
                        "endIndex": last_row_index + 1
                    }
                }
            }]
        }
        sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body=delete_request).execute()

        return last_row_data

    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_last_n_transactions(user_id: int, n: int = 1):
    """
    Fetches the last N rows from this user's Transactions tab (most
    recent last, same order as they appear in the sheet). Returns
    fewer than N rows if the tab has fewer than N rows total, or an
    empty list if there's no data (or no tab) at all.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A:E",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"
            ).execute()
        except HttpError as e:
            if e.resp.status == 400:
                return []
            raise
        rows = result.get("values", [])
        return rows[-n:] if rows else []

    return await asyncio.to_thread(_retry_call, sync_worker)


async def delete_last_n_transactions(user_id: int, n: int = 1):
    """
    Deletes the last N rows from this user's Transactions tab in one
    batch operation (used to undo a multi-entry save as a single
    unit).

    Returns:
        The deleted rows' data (list of lists), or an empty list if
        the tab had no data rows to delete.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        sheet_id = _get_sheet_id(sheet, tab_name)
        if sheet_id is None:
            return []

        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{tab_name}!A:E",
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING"
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return []

        n_actual = min(n, len(rows))
        deleted_rows = rows[-n_actual:]
        start_index = len(rows) - n_actual
        end_index = len(rows)

        delete_request = {
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id, "dimension": "ROWS",
                        "startIndex": start_index, "endIndex": end_index
                    }
                }
            }]
        }
        sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body=delete_request).execute()

        return deleted_rows

    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_all_transactions(user_id: int):
    """
    Asynchronously fetches every transaction row from this user's tab.
    Used for aggregation features (e.g. the monthly /report command).

    Returns:
        A list of rows, each [date, type_tr, category, amount, description].
        Empty list if the tab has no data (or doesn't exist) yet.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A:E",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"
            ).execute()
            return result.get("values", [])
        except HttpError as e:
            if e.resp.status == 400:
                return []
            raise

    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_recent_transactions_with_index(user_id: int, n: int = 10):
    """
    Fetches the last N transaction rows from this user's tab together
    with each row's 0-based sheet row index (where row 0 is the header
    row) — that index is what update_transaction_row and
    delete_transaction_row below need to target a *specific* row,
    rather than only ever the very last one like delete_last_transaction
    does. Used to power /edit, which lets a person pick any of their
    recent entries rather than only the most recent.

    Returns:
        A list of (row_index, [date, type_tr, category, amount, description])
        tuples, most recent last. Empty list if there's no data yet.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A:E",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"
            ).execute()
        except HttpError as e:
            if e.resp.status == 400:
                return []
            raise

        rows = result.get("values", [])
        if len(rows) <= 1:
            return []  # header only, or no tab data at all

        data_rows = rows[1:]  # skip the header row
        # Row index i in `rows` corresponds directly to a 0-based sheet
        # row (row 0 = header), so a data row at position j in
        # data_rows sits at sheet row index j + 1.
        indexed = [(i + 1, row) for i, row in enumerate(data_rows)]
        return indexed[-n:]

    return await asyncio.to_thread(_retry_call, sync_worker)


async def update_transaction_row(user_id: int, row_index: int, date: str, type_tr: str, category: str, amount: float, description: str):
    """
    Overwrites a specific transaction row (identified by the 0-based
    sheet row index returned from get_recent_transactions_with_index)
    with new values. Used by /edit to save a change to an existing
    entry without needing to delete and re-append it.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)
        # A1 notation is 1-indexed, and row 1 is the header, so sheet
        # row index N (0-based, N=0 is the header) maps to A1 row N+1.
        sheet_row = row_index + 1

        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{tab_name}!A{sheet_row}:E{sheet_row}",
            valueInputOption="USER_ENTERED",
            body={"values": [[date, type_tr, category, amount, description]]}
        ).execute()

    return await asyncio.to_thread(_retry_call, sync_worker)


async def delete_transaction_row(user_id: int, row_index: int) -> bool:
    """
    Deletes a specific transaction row by its 0-based sheet row index.
    Returns True if deleted, False if this user has no Transactions
    tab at all (shouldn't normally happen if row_index came from a
    real fetch, but guards against a stale/expired /edit session).
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)

        sheet_id = _get_sheet_id(sheet, tab_name)
        if sheet_id is None:
            return False

        sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id, "dimension": "ROWS",
                        "startIndex": row_index, "endIndex": row_index + 1
                    }
                }
            }]
        }).execute()
        return True

    return await asyncio.to_thread(_retry_call, sync_worker)


def _ensure_budgets_sheet_exists(sheet, tab_name: str):
    """
    Creates a budgets tab with a header row if it doesn't exist yet.
    Called lazily from set_budget — the tab isn't needed until someone
    actually sets their first limit.
    """
    if _tab_exists(sheet, tab_name):
        return

    sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "requests": [{"addSheet": {"properties": {"title": tab_name}}}]
    }).execute()
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A1:B1",
        valueInputOption="USER_ENTERED",
        body={"values": [["Category", "MonthlyLimit"]]}
    ).execute()


async def get_budgets(user_id: int):
    """
    Fetches all configured budget rows [category, limit] from this
    user's Budgets tab. Returns an empty list if the tab doesn't
    exist yet (i.e. no budgets have been set) instead of raising.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Budgets", user_id)
        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A2:B",  # skip the header row
                valueRenderOption="UNFORMATTED_VALUE"
            ).execute()
            return result.get("values", [])
        except HttpError as e:
            if e.resp.status == 400:
                # Range refers to a sheet tab that doesn't exist yet.
                return []
            raise

    return await asyncio.to_thread(_retry_call, sync_worker)


async def set_budget(user_id: int, category: str, limit: float):
    """
    Creates or updates the monthly limit for a category in this
    user's Budgets tab (creating the tab itself on first use).
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Budgets", user_id)
        _ensure_budgets_sheet_exists(sheet, tab_name)

        existing = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A2:A"
        ).execute().get("values", [])

        row_number = None
        for i, row in enumerate(existing):
            if row and row[0].strip().lower() == category.strip().lower():
                row_number = i + 2  # +1 for header, +1 for 1-based indexing
                break

        if row_number:
            sheet.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A{row_number}:B{row_number}",
                valueInputOption="USER_ENTERED",
                body={"values": [[category, limit]]}
            ).execute()
        else:
            sheet.values().append(
                spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A:B",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body={"values": [[category, limit]]}
            ).execute()

    return await asyncio.to_thread(_retry_call, sync_worker)


async def delete_budget(user_id: int, category: str) -> bool:
    """
    Removes a category's budget row from this user's Budgets tab.
    Returns True if a row was found and deleted, False if there was
    no budget set for that category.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Budgets", user_id)

        sheet_id = _get_sheet_id(sheet, tab_name)
        if sheet_id is None:
            return False  # no Budgets tab at all yet for this user

        existing = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A2:A"
        ).execute().get("values", [])

        row_number = None
        for i, row in enumerate(existing):
            if row and row[0].strip().lower() == category.strip().lower():
                row_number = i + 1  # 0-based sheet row index (header is row 0)
                break

        if row_number is None:
            return False

        sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
            "requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id, "dimension": "ROWS",
                        "startIndex": row_number, "endIndex": row_number + 1
                    }
                }
            }]
        }).execute()
        return True

    return await asyncio.to_thread(_retry_call, sync_worker)
