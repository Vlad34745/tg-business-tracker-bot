import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Імпортуємо головний роутер з хендлерів
from core.handlers import router as main_router

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

# Налаштування логування в консоль
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
        logger.critical("BOT_TOKEN missing in .env file! Скрипт зупинено.")
        return

    # Ініціалізація бота
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    # Диспетчер
    dp = Dispatcher()

    # Підключаємо наш єдиний роутер з хендлерами
    dp.include_router(main_router)

    logger.info("Бот успішно запускається... Починаємо опитування (Polling).")
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Сесію бота закрито. Роботу завершено.")

if __name__ == "__main__":
    # Для Python 3.14 на Windows прибираємо застарілу політику, asyncio.run() тепер сам усе робить красиво
    asyncio.run(main())