"""
Transaction row CRUD against each user's Transactions_<id> tab (or the
plain "Transactions" tab for the owner — see core.storage._client._tab_name).
"""
import asyncio
from googleapiclient.errors import HttpError
from core.storage._client import (
    SPREADSHEET_ID, _get_sheets_service, _retry_call, _tab_name,
    _tab_exists, _get_sheet_id, _known_existing_tabs
)


def _ensure_transactions_sheet_exists(sheet, tab_name: str):
    """
    Creates a transactions tab with a header row if it doesn't exist
    yet. Called lazily from append_transaction — a brand new user's
    tab isn't created until their first saved entry. Short-circuits
    via _known_existing_tabs once a tab has been confirmed once in
    this process, to avoid a metadata round trip on every single call.
    """
    if tab_name in _known_existing_tabs:
        return
    if _tab_exists(sheet, tab_name):
        _known_existing_tabs.add(tab_name)
        return

    sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "requests": [{"addSheet": {"properties": {"title": tab_name}}}]
    }).execute()
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A1:E1",
        valueInputOption="USER_ENTERED",
        body={"values": [["Date", "Type", "Category", "Amount", "Description"]]}
    ).execute()
    _known_existing_tabs.add(tab_name)


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

        try:
            return sheet.values().append(
                spreadsheetId=SPREADSHEET_ID, range=range_name,
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
        except HttpError as e:
            if e.resp.status == 400 and tab_name in _known_existing_tabs:
                # The cache said this tab exists, but the API just told
                # us otherwise — most likely someone deleted the tab by
                # hand directly in Google Sheets. Evict the stale cache
                # entry, recreate the tab, and retry once, rather than
                # surfacing a confusing error for something the bot can
                # self-heal.
                _known_existing_tabs.discard(tab_name)
                _ensure_transactions_sheet_exists(sheet, tab_name)
                return sheet.values().append(
                    spreadsheetId=SPREADSHEET_ID, range=range_name,
                    valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                    body=body
                ).execute()
            raise

    # Offload the synchronous API call to a separate background thread
    return await asyncio.to_thread(_retry_call, sync_worker)


async def append_transactions_batch(user_id: int, entries: list):
    """
    Appends multiple transaction rows in a single API call, rather
    than one call per entry. Used by the multi-line "save all"
    confirmation flow — looping append_transaction() once per row made
    saving a large batch (10-20+ lines pasted at once) noticeably slow,
    since each call is a separate network round trip (and, before the
    _known_existing_tabs cache above, an extra metadata round trip on
    top of that). One batched call cuts this down to a single request
    no matter how many rows are in the batch.

    `entries` is a list of dicts, each with date/type_tr/category/
    amount/description keys (the same shape as a single pending entry
    used elsewhere in the batch-confirm flow).

    This call is all-or-nothing: if it fails, none of the rows are
    saved (unlike the old per-entry loop, which could partially
    succeed). That trade-off is intentional — a single atomic request
    is both faster and avoids the confusing "10 of 19 saved, here's
    which ones failed" state a partial loop could leave behind.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)
        _ensure_transactions_sheet_exists(sheet, tab_name)

        row_values = [
            [e["date"], e["type_tr"], e["category"], e["amount"], e["description"]]
            for e in entries
        ]
        append_kwargs = dict(
            spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A:E",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
            body={"values": row_values}
        )

        try:
            sheet.values().append(**append_kwargs).execute()
        except HttpError as e:
            if e.resp.status == 400 and tab_name in _known_existing_tabs:
                # Same self-heal as append_transaction: the tab was
                # apparently deleted by hand since we last confirmed it
                # existed. Evict the cache, recreate it, and retry once.
                _known_existing_tabs.discard(tab_name)
                _ensure_transactions_sheet_exists(sheet, tab_name)
                sheet.values().append(**append_kwargs).execute()
            else:
                raise

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


async def get_all_transactions_with_index(user_id: int):
    """
    Fetches every transaction row from this user's tab together with
    each row's 0-based sheet row index (row 0 = header) — the index
    that update_transaction_row/delete_transaction_row need to target
    a specific row. Used by /find to offer an "✑️ Edit" button next to
    each search result, not just the most recent entries.

    Returns:
        A list of (row_index, [date, type_tr, category, amount, description])
        tuples, oldest first (same order as the sheet). Empty list if
        the tab has no data (or doesn't exist) yet.
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
            return []

        data_rows = rows[1:]
        return [(i + 1, row) for i, row in enumerate(data_rows)]

    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_recent_transactions_with_index(user_id: int, n: int = 10, offset: int = 0):
    """
    Fetches a page of N transaction rows from this user's tab, counting
    back from the most recent, together with each row's 0-based sheet
    row index (where row 0 is the header row) — that index is what
    update_transaction_row and delete_transaction_row below need to
    target a *specific* row, rather than only ever the very last one
    like delete_last_transaction does. Used to power /edit, which lets
    a person pick any of their recent entries rather than only the
    most recent.

    `offset` skips that many of the most recent entries before taking
    the page of N — offset=0 is the most recent page, offset=10 is the
    page before that, and so on. Used for the "⬇️ Older" pagination
    button in /edit.

    Returns:
        (page, has_more) — page is a list of
        (row_index, [date, type_tr, category, amount, description])
        tuples for this page, most recent last. has_more is True if
        there are still older entries beyond this page (i.e. another
        "⬇️ Older" tap would return more). Empty page + has_more=False
        if there's no data (or no more pages) at all.
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
                return [], False
            raise

        rows = result.get("values", [])
        if len(rows) <= 1:
            return [], False  # header only, or no tab data at all

        data_rows = rows[1:]  # skip the header row
        # Row index i in `rows` corresponds directly to a 0-based sheet
        # row (row 0 = header), so a data row at position j in
        # data_rows sits at sheet row index j + 1.
        indexed = [(i + 1, row) for i, row in enumerate(data_rows)]

        end = len(indexed) - offset
        start = max(0, end - n)
        page = indexed[start:end] if end > 0 else []
        has_more = start > 0
        return page, has_more

    return await asyncio.to_thread(_retry_call, sync_worker)


async def get_transaction_row(user_id: int, row_index: int):
    """
    Fetches the current values of a single transaction row by its
    0-based sheet row index. Used by /edit to verify a row still holds
    the data it showed the person before committing an update or
    delete against it — if someone deletes an earlier row in between,
    every row after it shifts up by one, which would otherwise make
    /edit silently overwrite or delete the wrong transaction.

    Returns the row as a list, or None if that row no longer exists
    (out of range, or the tab itself is gone).
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        tab_name = _tab_name("Transactions", user_id)
        sheet_row = row_index + 1  # A1 notation is 1-indexed; row 1 is the header.

        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{tab_name}!A{sheet_row}:E{sheet_row}",
                valueRenderOption="UNFORMATTED_VALUE",
                dateTimeRenderOption="FORMATTED_STRING"
            ).execute()
        except HttpError as e:
            if e.resp.status == 400:
                return None
            raise

        rows = result.get("values", [])
        return rows[0] if rows else None

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


