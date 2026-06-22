import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Import the main router from handlers
from core.handlers import router as main_router

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

# Configure logging output to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

async def main():
    if not TOKEN:
        logger.critical("BOT_TOKEN missing in .env file! Script stopped.")
        return

    # Initialize bot instance
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Initialize dispatcher
    dp = Dispatcher()

    # Include the main routing layer
    dp.include_router(main_router)

    logger.info("Bot is starting up... Beginning long polling.")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot session closed. Shutdown complete.")

if __name__ == "__main__":
    # Standard asyncio loop execution
    asyncio.run(main())