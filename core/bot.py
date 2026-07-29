import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from dotenv import load_dotenv

# Import the main router from handlers
from core.handlers import router as main_router, ALLOWED_IDS
from core.reminder import reminder_loop

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

    # Register the command list so Telegram shows it in the "/" menu
    await bot.set_my_commands([
        BotCommand(command="start", description="Почати роботу з ботом"),
        BotCommand(command="last", description="Показати останній запис"),
        BotCommand(command="undo", description="Видалити останній запис"),
        BotCommand(command="report", description="Звіт: місяць/тиждень/день (напр. /report 7d)"),
        BotCommand(command="budget", description="Ліміти по категоріях (/budget set Кафе 1000)"),
        BotCommand(command="export", description="Експорт усіх записів у CSV"),
        BotCommand(command="find", description="Пошук записів (/find кафе)"),
        BotCommand(command="remind", description="Нагадування о 21:00 (/remind on/off)"),
    ])

    # Background task: sends a daily reminder to log expenses if enabled
    if ALLOWED_IDS:
        asyncio.create_task(reminder_loop(bot, ALLOWED_IDS))
    else:
        logger.warning("ALLOWED_USER_ID not set — daily reminder task not started.")

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