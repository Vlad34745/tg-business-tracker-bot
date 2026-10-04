import pytest

from core import access, language
from core.handlers import start as h
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data


# --- /start ---

@pytest.mark.asyncio
async def test_start_for_owner_sends_welcome_and_menu_without_registering():
    message = make_message(OWNER_ID, "/start")

    await h.cmd_start(message)

    assert message.answer.await_count == 2  # the 👋 + the welcome text
    welcome_call = message.answer.await_args_list[1]
    assert "Привіт" in welcome_call.args[0]
    assert "nav:report" in keyboard_callback_data(welcome_call.kwargs["reply_markup"])
    assert access.count() == 0  # owners never self-register


@pytest.mark.asyncio
async def test_start_registers_a_stranger_and_applies_command_menu():
    message = make_message(STRANGER_ID, "/start")
    assert _shared.is_owner(STRANGER_ID) is False

    await h.cmd_start(message)

    assert access.is_registered(STRANGER_ID) is True
    assert _shared.is_owner(STRANGER_ID) is True
    message.bot.set_my_commands.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_uses_english_text_for_english_users():
    language.set_language(OWNER_ID, "en")
    message = make_message(OWNER_ID, "/start")

    await h.cmd_start(message)

    assert "Finance Tracker" in message.answer.await_args_list[1].args[0]


# --- /language ---

@pytest.mark.asyncio
async def test_language_denied_for_stranger():
    message = make_message(STRANGER_ID, "/language")
    await h.cmd_language(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


@pytest.mark.asyncio
async def test_language_shows_picker_for_owner():
    message = make_message(OWNER_ID, "/language")
    await h.cmd_language(message)
    markup = message.answer.await_args.kwargs["reply_markup"]
    assert keyboard_callback_data(markup) == ["lang_set:uk", "lang_set:en"]


@pytest.mark.asyncio
async def test_set_language_callback_switches_language():
    callback = make_callback(OWNER_ID, "lang_set:en")

    await h.cb_set_language(callback)

    assert language.get_language(OWNER_ID) == "en"
    callback.bot.set_my_commands.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once_with(t("language_set", "en"))


@pytest.mark.asyncio
async def test_set_language_callback_denied_for_stranger():
    callback = make_callback(STRANGER_ID, "lang_set:en")

    await h.cb_set_language(callback)

    assert language.get_language(STRANGER_ID) == "uk"  # unchanged
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_nav_language_shows_picker_for_owner():
    callback = make_callback(OWNER_ID, "nav:language")
    await h.cb_nav_language(callback)
    markup = callback.message.answer.await_args.kwargs["reply_markup"]
    assert keyboard_callback_data(markup) == ["lang_set:uk", "lang_set:en"]


@pytest.mark.asyncio
async def test_nav_language_denied_for_stranger():
    callback = make_callback(STRANGER_ID, "nav:language")
    await h.cb_nav_language(callback)
    assert callback.answer.await_args.kwargs == {"show_alert": True}
    callback.message.answer.assert_not_awaited()


# --- /stats ---

@pytest.mark.asyncio
async def test_stats_shows_counts_to_admin():
    access.register(1)
    access.register(2)
    message = make_message(OWNER_ID, "/stats")

    await h.cmd_stats(message)

    expected = t("stats_text", "uk", static=1, auto=2, total=3)
    message.answer.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_stats_denied_even_for_self_registered_users():
    access.register(STRANGER_ID)
    message = make_message(STRANGER_ID, "/stats")

    await h.cmd_stats(message)

    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


# --- /cancel ---

@pytest.mark.asyncio
async def test_cancel_denied_for_stranger():
    message = make_message(STRANGER_ID, "/cancel")
    await h.cmd_cancel(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


@pytest.mark.asyncio
async def test_cancel_with_nothing_pending():
    message = make_message(OWNER_ID, "/cancel")
    await h.cmd_cancel(message)
    message.answer.assert_awaited_once_with(t("cancel_nothing", "uk"))


@pytest.mark.asyncio
async def test_cancel_clears_active_flow():
    _shared.awaiting_find_query[OWNER_ID] = True
    message = make_message(OWNER_ID, "/cancel")

    await h.cmd_cancel(message)

    message.answer.assert_awaited_once_with(t("cancel_done", "uk"))
    assert OWNER_ID not in _shared.awaiting_find_query
