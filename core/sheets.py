import os
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

def _get_sheets_service():
    """Internal synchronous helper to initialize the Google Sheets API client."""
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(f"Google credentials file missing at: {CREDENTIALS_PATH}")
    
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)

async def append_transaction(date: str, type_tr: str, category: str, amount: float, description: str):
    """
    Asynchronously appends a transaction row into the Google Sheet.
    Uses asyncio.to_thread to prevent API requests from blocking the bot's main loop.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        
        # Prepare the row values for inserting
        row_values = [[date, type_tr, category, amount, description]]
        body = {"values": row_values}
        
        # Target sheet tab name and column range
        range_name = "Transactions!A:E"
        
        request = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        )
        return request.execute()

    # Offload the synchronous API call to a separate background thread
    return await asyncio.to_thread(sync_worker)


async def get_last_transaction():
    """
    Asynchronously fetches the most recently added transaction row
    from the Google Sheet.

    Returns:
        A list [date, type_tr, category, amount, description] for the
        last row, or None if the sheet has no data rows yet.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()

        range_name = "Transactions!A:E"
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING"
        ).execute()
        return result.get("values", [])

    rows = await asyncio.to_thread(sync_worker)
    if not rows:
        return None

    return rows[-1]


async def delete_last_transaction():
    """
    Asynchronously deletes the last row from the Transactions sheet.

    Returns:
        The deleted row's data as a list, or None if the sheet had no
        data rows to delete.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()

        # Find the numeric sheetId for the "Transactions" tab —
        # required by the batchUpdate deleteDimension request below.
        metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_id = None
        for tab in metadata.get("sheets", []):
            if tab["properties"]["title"] == "Transactions":
                sheet_id = tab["properties"]["sheetId"]
                break
        if sheet_id is None:
            raise ValueError("Sheet tab 'Transactions' not found in the spreadsheet.")

        range_name = "Transactions!A:E"
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
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

    return await asyncio.to_thread(sync_worker)


async def get_all_transactions():
    """
    Asynchronously fetches every transaction row from the sheet.
    Used for aggregation features (e.g. the monthly /report command).

    Returns:
        A list of rows, each [date, type_tr, category, amount, description].
        Empty list if the sheet has no data yet.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()

        range_name = "Transactions!A:E"
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING"
        ).execute()
        return result.get("values", [])

    return await asyncio.to_thread(sync_worker)


def _ensure_budgets_sheet_exists(sheet):
    """
    Creates the "Budgets" tab with a header row if it doesn't exist yet.
    Called lazily from set_budget — the tab isn't needed until someone
    actually sets their first limit.
    """
    metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [tab["properties"]["title"] for tab in metadata.get("sheets", [])]
    if "Budgets" in titles:
        return

    sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body={
        "requests": [{"addSheet": {"properties": {"title": "Budgets"}}}]
    }).execute()
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID, range="Budgets!A1:B1",
        valueInputOption="USER_ENTERED",
        body={"values": [["Category", "MonthlyLimit"]]}
    ).execute()


async def get_budgets():
    """
    Fetches all configured budget rows [category, limit] from the
    "Budgets" tab. Returns an empty list if the tab doesn't exist yet
    (i.e. no budgets have been set) instead of raising.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        try:
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Budgets!A2:B",  # skip the header row
                valueRenderOption="UNFORMATTED_VALUE"
            ).execute()
            return result.get("values", [])
        except HttpError as e:
            if e.resp.status == 400:
                # Range refers to a sheet tab that doesn't exist yet.
                return []
            raise

    return await asyncio.to_thread(sync_worker)


async def set_budget(category: str, limit: float):
    """
    Creates or updates the monthly limit for a category in the
    "Budgets" tab (creating the tab itself on first use).
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        _ensure_budgets_sheet_exists(sheet)

        existing = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID, range="Budgets!A2:A"
        ).execute().get("values", [])

        row_number = None
        for i, row in enumerate(existing):
            if row and row[0].strip().lower() == category.strip().lower():
                row_number = i + 2  # +1 for header, +1 for 1-based indexing
                break

        if row_number:
            sheet.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"Budgets!A{row_number}:B{row_number}",
                valueInputOption="USER_ENTERED",
                body={"values": [[category, limit]]}
            ).execute()
        else:
            sheet.values().append(
                spreadsheetId=SPREADSHEET_ID, range="Budgets!A:B",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                body={"values": [[category, limit]]}
            ).execute()

    return await asyncio.to_thread(sync_worker)


async def delete_budget(category: str) -> bool:
    """
    Removes a category's budget row. Returns True if a row was found
    and deleted, False if there was no budget set for that category.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()

        metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_id = None
        for tab in metadata.get("sheets", []):
            if tab["properties"]["title"] == "Budgets":
                sheet_id = tab["properties"]["sheetId"]
                break
        if sheet_id is None:
            return False  # no Budgets tab at all yet

        existing = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID, range="Budgets!A2:A"
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

    return await asyncio.to_thread(sync_worker)