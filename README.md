# Telegram Business & P&L Finance Tracker Bot 📊💰

A lightweight, secure, and production-ready Telegram Bot built with **aiogram 3.x** and **Google Sheets API**. It allows users to instantly log income and expenses into a remote Google Spreadsheet directly from their smartphones using clean Python regex parsing (no heavy AI overhead).

## ✨ Features
- 🚀 **Instant Logging:** Send messages like `150 Food` or `12000 Freelance Upwork` to automatically categorize and log entries.
- 🔒 **Multi-User Access Control:** Secure access locked to specific Telegram User IDs via environment variables.
- 📉 **Automated Categorization:** Automatically distinguishes between `Income` and `Expense` based on customizable keywords.
- 📊 **Google Sheets Integration:** Non-blocking asynchronous data appending to Google Spreadsheet rows.
- 🔎 **`/last` Command:** Quickly check the most recently logged transaction without opening the spreadsheet.
- 🗑️ **`/undo` Command:** Delete the last transaction with an inline confirmation step to prevent accidental removal.
- 📊 **`/report` Command:** Monthly income/expense summary with balance and top spending categories, computed directly from the spreadsheet.
- Windows automation setup included via batch scripting (`.bat`).

## 🛠️ Tech Stack
- **Language:** Python 3.14+
- **Framework:** Aiogram 3.x (Async Telegram Bot API)
- **Database/Storage:** Google Sheets API (`google-api-python-client`), wrapped with `asyncio.to_thread` for non-blocking calls
- **Environment:** Python-dotenv, RegEx

## 🚀 Quick Start for Clients
1. Share your Google Sheet with your Google Service Account email.
2. Put your `credentials.json` into the root directory.
3. Setup your `.env` file with `BOT_TOKEN` and `ALLOWED_USER_ID` (see `.env.example` for the required format).
4. Install dependencies: `pip install -r requirements.txt`
5. Run the bot using `python -m core.bot` or launch via Windows `finance_bot.bat`.

## 🧪 Running Tests
Unit tests cover the message-parsing logic in `core/validator.py` and the monthly report aggregation in `core/report.py`:
```
pip install pytest
pytest tests/ -v
```