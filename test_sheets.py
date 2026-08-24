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