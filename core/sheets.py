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