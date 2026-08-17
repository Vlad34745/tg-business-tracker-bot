import json
import logging
import os
from aiogram.types import BotCommand

logger = logging.getLogger(__name__)

_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "language_settings.json"
)

SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"

# user_id (str) -> language code
_state: dict = {}


def _load_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _state.update({str(k): v for k, v in data.items() if v in SUPPORTED_LANGUAGES})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # no saved settings yet — keep defaults


def _save_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Failed to save language settings: {e}")


_load_settings()


def get_language(user_id: int) -> str:
    return _state.get(str(user_id), DEFAULT_LANGUAGE)


def set_language(user_id: int, lang: str) -> None:
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported language: {lang}")
    _state[str(user_id)] = lang
    _save_settings()


# Command menu ("/" list Telegram shows) per language. Kept here rather
# than in core/bot.py so both the startup registration and the
# per-chat override in /language's callback (in core/handlers.py) can
# share the same source of truth.
COMMANDS_UK = [
    BotCommand(command="start", description="Почати роботу з ботом"),
    BotCommand(command="last", description="Показати останній запис"),
    BotCommand(command="undo", description="Видалити останній запис"),
    BotCommand(command="edit", description="Редагувати або видалити будь-який з останніх записів"),
    BotCommand(command="report", description="Звіт — оберу період і категорії кнопками"),
    BotCommand(command="budget", description="Ліміти по категоріях — керування кнопками"),
    BotCommand(command="export", description="Експорт усіх записів у CSV"),
    BotCommand(command="find", description="Пошук: категорії кнопками або /find текст"),
    BotCommand(command="remind", description="Нагадування — час(и) і увімк/вимк кнопками"),
    BotCommand(command="language", description="Мова бота / Bot language"),
]
COMMANDS_EN = [
    BotCommand(command="start", description="Start using the bot"),
    BotCommand(command="last", description="Show the last entry"),
    BotCommand(command="undo", description="Delete the last entry"),
    BotCommand(command="edit", description="Edit or delete any of your recent entries"),
    BotCommand(command="report", description="Report — pick period and categories with buttons"),
    BotCommand(command="budget", description="Category limits — managed with buttons"),
    BotCommand(command="export", description="Export all entries to CSV"),
    BotCommand(command="find", description="Search: category buttons or /find text"),
    BotCommand(command="remind", description="Reminders — time(s) and on/off with buttons"),
    BotCommand(command="language", description="Bot language / Мова бота"),
]


def get_commands(lang: str) -> list:
    return COMMANDS_EN if lang == "en" else COMMANDS_UK


async def apply_commands_for_chat(bot, user_id: int) -> None:
    """
    Push the "/" command menu for this specific chat, matching the
    user's current /language setting — overriding whatever menu
    Telegram would otherwise show based on the user's own client
    language. Called right after /language changes, and once at
    startup for every user with a saved language so the override
    survives a bot restart.
    """
    from aiogram.types import BotCommandScopeChat
    try:
        await bot.set_my_commands(
            get_commands(get_language(user_id)),
            scope=BotCommandScopeChat(chat_id=user_id)
        )
    except Exception as e:
        logger.warning(f"Failed to set per-chat commands for {user_id}: {e}")