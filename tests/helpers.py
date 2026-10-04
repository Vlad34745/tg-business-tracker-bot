"""Tiny fakes for aiogram's Message / CallbackQuery, so handlers can be
called directly in tests without a running bot or Telegram connection."""
from unittest.mock import AsyncMock, MagicMock

OWNER_ID = 123      # listed in ALLOWED_USER_ID (see conftest.py)
STRANGER_ID = 999   # not allowed until they press /start


def make_message(user_id: int = 123, text: str = ""):
    message = MagicMock()
    message.from_user.id = user_id
    message.text = text
    message.answer = AsyncMock()
    message.answer_document = AsyncMock()
    message.answer_photo = AsyncMock()
    message.bot = MagicMock()
    message.bot.set_my_commands = AsyncMock()
    return message


def make_callback(user_id: int = 123, data: str = ""):
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.data = data
    callback.answer = AsyncMock()
    callback.bot = MagicMock()
    callback.bot.set_my_commands = AsyncMock()
    callback.message = MagicMock()
    callback.message.answer = AsyncMock()
    callback.message.answer_document = AsyncMock()
    callback.message.answer_photo = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.message.edit_reply_markup = AsyncMock()
    return callback


def keyboard_callback_data(markup) -> list:
    """Flat list of every callback_data in an InlineKeyboardMarkup."""
    return [button.callback_data for row in markup.inline_keyboard for button in row]
