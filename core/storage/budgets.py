"""
Budget-limit row CRUD against each user's Budgets_<id> tab (or the
plain "Budgets" tab for the owner — see core.storage._client._tab_name).
"""
import asyncio
from googleapiclient.errors import HttpError
from core.storage._client import (
    SPREADSHEET_ID, _get_sheets_service, _retry_call, _tab_name,
    _tab_exists, _get_sheet_id, _known_existing_tabs
)


def _ensure_budgets_sheet_exists(sheet, tab_name: str):
    """
    Creates a budgets tab with a header row if it doesn't exist yet.
    Called lazily from set_budget — the tab isn't needed until someone
    actually sets their first limit. Shares the same _known_existing_tabs
    cache as _ensure_transactions_sheet_exists, since tab names never
    collide between the two (different base names).
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
        spreadsheetId=SPREADSHEET_ID, range=f"{tab_name}!A1:B1",
        valueInputOption="USER_ENTERED",
        body={"values": [["Category", "MonthlyLimit"]]}
    ).execute()
    _known_existing_tabs.add(tab_name)


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

        def do_write():
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

        try:
            do_write()
        except HttpError as e:
            if e.resp.status == 400 and tab_name in _known_existing_tabs:
                # Same self-heal as append_transaction: the Budgets tab
                # was apparently deleted by hand since we last confirmed
                # it existed. Evict the cache, recreate it, and retry once.
                _known_existing_tabs.discard(tab_name)
                _ensure_budgets_sheet_exists(sheet, tab_name)
                do_write()
            else:
                raise

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
