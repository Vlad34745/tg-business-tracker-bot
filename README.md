# Telegram Business & P&L Finance Tracker Bot 📊💰

A lightweight, secure, and production-ready Telegram Bot built with **aiogram 3.x** and **Google Sheets API**. It allows users to instantly log income and expenses into a remote Google Spreadsheet directly from their smartphones using clean Python regex parsing (no heavy AI overhead).

## ✨ Features
- 🚀 **Instant Logging:** Send messages like `150 Food` or `12000 Freelance Upwork` to automatically categorize and log entries.
- ✅ **Confirm Before Save:** Every parsed entry is shown as a preview with inline ✅/❌ buttons before it's written to the sheet.
- ✏️ **Edit Category On the Fly:** Pick from your most-used categories via quick buttons, or type a custom one, right from the confirmation preview.
- ⚠️ **Duplicate Warning:** Flags a pending entry if a transaction with the same type/category/amount was saved in the last 2 minutes, to catch accidental double-sends.
- 💼 **`/budget` Command:** Set monthly spending limits per category (`/budget set Кафе 1000`), see current usage vs. limits, and get an over-budget warning inside `/report`.
- 📈 **Report Chart:** Every `/report` sends a horizontal bar chart image of the top spending categories alongside the text summary.
- 📄 **`/export` Command:** Downloads every transaction as a CSV file (UTF-8 with BOM, opens correctly in Excel with Cyrillic text).
- 🔍 **`/find` Command:** Search transactions by category or description (`/find кафе`), with a running total of matches.
- 🔔 **`/remind` Command:** Optional daily reminder (21:00) to log expenses, toggled with `/remind on` / `/remind off`.
- ✨ **Multi-Entry Input:** Paste several transactions at once (one per line) and confirm/save them all together.
- ⚡ **Quick-Action Menu:** Inline buttons (📋 Останній, 🗑 Undo, 📊 Звіт, 💼 Бюджет, 📄 Експорт) attached to key responses for one-tap navigation, without a permanently-visible bottom keyboard.
- 🔒 **Multi-User Access Control:** Secure access locked to specific Telegram User IDs via environment variables.
- 📉 **Automated Categorization:** Automatically distinguishes between `Income` and `Expense` based on customizable keywords.
- 📊 **Google Sheets Integration:** Non-blocking asynchronous data appending to Google Spreadsheet rows.
- 🔎 **`/last` Command:** Quickly check the most recently logged transaction without opening the spreadsheet.
- 🗑️ **`/undo` Command:** Delete the last transaction with an inline confirmation step to prevent accidental removal. If the last save was a multi-entry batch, undoes the whole batch as one unit.
- 📊 **`/report` Command:** Plain `/report` (typed, tapped from the Menu button, or "📊 Звіт") shows a two-step button picker — period first (today/week/month/2 months/year/custom), then category count (Top-5/10/15/full list/custom number) — no typing needed. Passing arguments still works directly: `/report 6` (June), `/report 6 2026`, `/report today`, `/report week`, `/report 12d` (last 12 days), `/report 2week`, `/report 2month` (calendar-accurate), `/report year`, `full`, `topN`. The chart image always matches the chosen category count.
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
Unit tests cover the message-parsing logic in `core/validator.py`, monthly report aggregation in `core/report.py`, budget comparison in `core/budget.py`, chart generation in `core/chart.py`, CSV export in `core/export.py`, and search filtering in `core/search.py`:
```
pip install pytest
pytest tests/ -v
```