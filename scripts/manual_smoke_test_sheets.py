"""
Manual smoke test — NOT part of the pytest suite (see tests/test_sheets.py
for the real automated tests, which mock the Sheets API).

Run this by hand when you want to confirm the bot can actually write to
your real Google Sheet end-to-end:
    python scripts/manual_smoke_test_sheets.py

Requires a working .env / credentials.json, and will append a real row
to your sheet. Replace user_id below with your own Telegram user ID first.
"""

import asyncio
from core.storage import append_transaction

async def main():
    print("[TEST] Sending data to Google Sheets...")
    try:
        await append_transaction(
            user_id=123456789,  # replace with your own Telegram user ID to test
            date="2026-06-20",
            type_tr="Expense",
            category="Software License",
            amount=15.00,
            description="Upwork proxy / Server hosting test"
        )
        print("[SUCCESS] Row appended! Check your Google Sheet under 'Transactions' tab.")
    except Exception as e:
        print(f"[ERROR] Failed to upload data: {e}")

if __name__ == "__main__":
    asyncio.run(main())