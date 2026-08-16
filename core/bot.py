import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Import the main router from handlers
from core.handlers import router as main_router, ALLOWED_IDS
from core.reminder import reminder_loop
from core import language
from core import access

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

    # Register the command list so Telegram shows it in the "/" menu.
    # The Ukrainian list is the global default (also used as the
    # fallback for language_code="en" clients that haven't picked a
    # bot language yet). On top of that, every user who already has a
    # saved /language preference gets a per-chat override via
    # BotCommandScopeChat, so the menu matches their bot-language
    # choice rather than their Telegram client's own language — this
    # also re-applies the override after a bot restart. Includes both
    # the static ALLOWED_USER_ID list and anyone who self-registered
    # via /start in a previous run.
    await bot.set_my_commands(language.COMMANDS_UK)
    all_configured_ids = list(dict.fromkeys(ALLOWED_IDS + list(access.all_ids())))
    for user_id_str in all_configured_ids:
        try:
            await language.apply_commands_for_chat(bot, int(user_id_str))
        except ValueError:
            continue

    # Background task: sends a daily reminder to log expenses if enabled.
    # get_user_ids is re-evaluated on every reminder cycle rather than
    # captured once here, so users who self-register via /start after
    # the bot has started still receive reminders without a restart.
    def _get_reminder_user_ids():
        return list(dict.fromkeys(ALLOWED_IDS + list(access.all_ids())))

    asyncio.create_task(reminder_loop(bot, _get_reminder_user_ids))

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