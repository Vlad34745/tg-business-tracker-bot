import os
import asyncio
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# Шлях до файлу ключів Google Service Account
CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# Область доступу (права на роботу з таблицями)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def _get_sheets_service():
    """Внутрішній синхронний помічник для ініціалізації Google Sheets клієнта."""
    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(f"Google credentials file missing at: {CREDENTIALS_PATH}")
    
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)

async def append_transaction(date: str, type_tr: str, category: str, amount: float, description: str):
    """
    Асинхронно додає рядок з транзакцією в Google Таблицю.
    Використовує asyncio.to_thread, щоб запити не блокували роботу бота.
    """
    def sync_worker():
        service = _get_sheets_service()
        sheet = service.spreadsheets()
        
        # Формуємо рядок для запису
        row_values = [[date, type_tr, category, amount, description]]
        body = {"values": row_values}
        
        # Назва нашої вкладки в таблиці
        range_name = "Transactions!A:E"
        
        request = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        )
        return request.execute()

    # Відправляємо синхронну задачу в окремий потік
    return await asyncio.to_thread(sync_worker)