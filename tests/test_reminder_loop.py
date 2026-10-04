import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from core import language, reminder
from core.i18n import t


def _fake_sleep(monkeypatch, stop_after: int):
    """Replace asyncio.sleep inside the reminder module: records every delay
    and raises CancelledError on call number `stop_after` to end the
    otherwise endless loop."""
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise asyncio.CancelledError

    monkeypatch.setattr(reminder.asyncio, "sleep", fake_sleep)
    return delays


def _bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# --- _send_one_reminder ---

@pytest.mark.asyncio
async def test_send_one_reminder_uses_the_users_language():
    language.set_language(42, "en")
    bot = _bot()
    await reminder._send_one_reminder(bot, "42")
    bot.send_message.assert_awaited_once_with(42, t("daily_reminder_text", "en"), parse_mode="HTML")


@pytest.mark.asyncio
async def test_send_one_reminder_failure_is_logged_not_raised(caplog):
    bot = _bot()
    bot.send_message.side_effect = RuntimeError("blocked by user")
    with caplog.at_level("WARNING"):
        await reminder._send_one_reminder(bot, "42")
    assert "Failed to send reminder to 42" in caplog.text


@pytest.mark.asyncio
async def test_send_one_reminder_with_invalid_user_id_is_logged(caplog):
    bot = _bot()
    with caplog.at_level("WARNING"):
        await reminder._send_one_reminder(bot, "not-a-number")
    bot.send_message.assert_not_awaited()
    assert "Failed to send reminder" in caplog.text


# --- reminder_loop ---

@pytest.mark.asyncio
async def test_loop_sends_to_every_user_when_enabled(monkeypatch):
    delays = _fake_sleep(monkeypatch, stop_after=3)
    bot = _bot()

    with pytest.raises(asyncio.CancelledError):
        await reminder.reminder_loop(bot, lambda: ["1", "2"])

    assert 0 < delays[0] <= 24 * 3600  # waited until the next configured time
    assert delays[1] == 60              # then a one-minute guard against double-firing
    assert sorted(call.args[0] for call in bot.send_message.await_args_list) == [1, 2]


@pytest.mark.asyncio
async def test_loop_sends_nothing_when_disabled(monkeypatch):
    reminder.set_enabled(False)
    _fake_sleep(monkeypatch, stop_after=3)
    bot = _bot()
    with pytest.raises(asyncio.CancelledError):
        await reminder.reminder_loop(bot, lambda: ["1"])
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_sends_nothing_when_there_are_no_users(monkeypatch):
    _fake_sleep(monkeypatch, stop_after=3)
    bot = _bot()
    with pytest.raises(asyncio.CancelledError):
        await reminder.reminder_loop(bot, lambda: [])
    bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_loop_falls_back_to_hourly_wait_when_no_times_configured(monkeypatch):
    reminder._state["times"] = []
    delays = _fake_sleep(monkeypatch, stop_after=1)
    with pytest.raises(asyncio.CancelledError):
        await reminder.reminder_loop(_bot(), lambda: [])
    assert delays[0] == pytest.approx(3600, abs=5)


@pytest.mark.asyncio
async def test_loop_picks_the_soonest_of_several_times(monkeypatch):
    reminder._state["times"] = ["00:00", "23:59"]
    delays = _fake_sleep(monkeypatch, stop_after=1)
    with pytest.raises(asyncio.CancelledError):
        await reminder.reminder_loop(_bot(), lambda: [])
    assert 0 < delays[0] <= 24 * 3600


# --- settings persistence failure ---

def test_save_settings_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(reminder, "_SETTINGS_PATH", str(tmp_path))  # a directory -> OSError on open()
    with caplog.at_level("WARNING"):
        reminder.set_enabled(False)
    assert reminder.is_enabled() is False  # still applied in memory
    assert "Failed to save reminder settings" in caplog.text
