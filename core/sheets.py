import os
import asyncio
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
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