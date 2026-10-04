import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from core import access, language
from core import bot as bot_module
from core.handlers import _shared


@pytest.fixture
def fake_aiogram(monkeypatch):
    """Replace Bot / Dispatcher / the reminder loop so main() can run without
    a real token, network, or never-ending polling."""
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    bot.delete_webhook = AsyncMock()
    bot.session.close = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.start_polling = AsyncMock()

    env = type("Env", (), {})()
    env.bot, env.dispatcher = bot, dispatcher
    env.bot_class = MagicMock(return_value=bot)
    env.reminder_loop = AsyncMock()
    env.apply_commands = AsyncMock()

    monkeypatch.setattr(bot_module, "TOKEN", "123:fake-token")
    monkeypatch.setattr(bot_module, "Bot", env.bot_class)
    monkeypatch.setattr(bot_module, "Dispatcher", MagicMock(return_value=dispatcher))
    monkeypatch.setattr(bot_module, "reminder_loop", env.reminder_loop)
    monkeypatch.setattr(language, "apply_commands_for_chat", env.apply_commands)
    return env


@pytest.mark.asyncio
async def test_main_stops_without_a_token(monkeypatch, caplog):
    monkeypatch.setattr(bot_module, "TOKEN", None)
    bot_class = MagicMock()
    monkeypatch.setattr(bot_module, "Bot", bot_class)

    with caplog.at_level("CRITICAL"):
        await bot_module.main()

    assert "BOT_TOKEN missing" in caplog.text
    bot_class.assert_not_called()


@pytest.mark.asyncio
async def test_main_wires_everything_up_and_closes_the_session(fake_aiogram):
    await bot_module.main()
    await asyncio.sleep(0)  # let the background reminder task start

    fake_aiogram.dispatcher.include_router.assert_called_once_with(bot_module.main_router)
    fake_aiogram.bot.set_my_commands.assert_awaited_once_with(language.COMMANDS_UK)
    fake_aiogram.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
    fake_aiogram.dispatcher.start_polling.assert_awaited_once_with(fake_aiogram.bot)
    fake_aiogram.bot.session.close.assert_awaited_once()
    fake_aiogram.reminder_loop.assert_called_once()


@pytest.mark.asyncio
async def test_main_applies_per_chat_command_menus_for_known_users(fake_aiogram):
    access.register(555)
    _shared.ALLOWED_IDS.append("not-a-number")  # must be skipped, not crash startup

    await bot_module.main()
    await asyncio.sleep(0)

    applied_ids = [call.args[1] for call in fake_aiogram.apply_commands.await_args_list]
    assert applied_ids == [123, 555]


@pytest.mark.asyncio
async def test_reminder_recipients_are_recomputed_on_every_call(fake_aiogram):
    await bot_module.main()
    await asyncio.sleep(0)
    get_user_ids = fake_aiogram.reminder_loop.call_args.args[1]

    assert get_user_ids() == ["123"]
    access.register(555)  # someone self-registers after startup
    assert get_user_ids() == ["123", "555"]
    access.register(123)  # an id listed twice is only reminded once
    assert get_user_ids() == ["123", "555"]


@pytest.mark.asyncio
async def test_main_closes_the_session_even_if_polling_crashes(fake_aiogram):
    fake_aiogram.dispatcher.start_polling.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError):
        await bot_module.main()

    fake_aiogram.bot.session.close.assert_awaited_once()
