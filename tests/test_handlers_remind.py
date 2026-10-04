import pytest

from core import reminder
from core.handlers import remind as h
from core.handlers import _shared
from core.i18n import t
from tests.helpers import OWNER_ID, STRANGER_ID, make_callback, make_message, keyboard_callback_data


def _denied(mock):
    return mock.answer.await_args.kwargs == {"show_alert": True}


# --- status helpers ---

def test_status_text_reflects_enabled_state_and_times():
    reminder.add_time("09:00")
    text = h._remind_status_text("uk")
    assert t("remind_status_on", "uk") in text
    assert "09:00, 21:00" in text


def test_status_text_when_disabled():
    reminder.set_enabled(False)
    assert t("remind_status_off", "uk") in h._remind_status_text("uk")


def test_menu_keyboard_has_all_actions():
    assert keyboard_callback_data(h._remind_menu_keyboard("uk")) == [
        "remind_on", "remind_off", "remind_add_time", "remind_remove_time"
    ]


# --- /remind text command ---

@pytest.mark.asyncio
async def test_remind_denied_for_stranger():
    message = make_message(STRANGER_ID, "/remind on")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("access_denied", "uk"))


@pytest.mark.asyncio
async def test_remind_without_args_shows_menu():
    message = make_message(OWNER_ID, "/remind")
    await h.cmd_remind(message)
    assert message.answer.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["on", "увімкнути"])
async def test_remind_on(word):
    reminder.set_enabled(False)
    message = make_message(OWNER_ID, f"/remind {word}")
    await h.cmd_remind(message)
    assert reminder.is_enabled() is True
    message.answer.assert_awaited_once_with(t("remind_on_msg", "uk"))


@pytest.mark.asyncio
@pytest.mark.parametrize("word", ["off", "вимкнути"])
async def test_remind_off(word):
    message = make_message(OWNER_ID, f"/remind {word}")
    await h.cmd_remind(message)
    assert reminder.is_enabled() is False
    message.answer.assert_awaited_once_with(t("remind_off_msg", "uk"))


@pytest.mark.asyncio
async def test_remind_add_new_time_normalizes_it():
    message = make_message(OWNER_ID, "/remind add 9:05")
    await h.cmd_remind(message)
    assert "09:05" in reminder.get_times()
    message.answer.assert_awaited_once_with(t("remind_time_added", "uk", time="09:05"))


@pytest.mark.asyncio
async def test_remind_add_existing_time():
    message = make_message(OWNER_ID, "/remind add 21:00")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("remind_time_exists", "uk", time="21:00"))


@pytest.mark.asyncio
async def test_remind_add_invalid_time_shows_format_hint():
    message = make_message(OWNER_ID, "/remind add 25:99")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("remind_format_hint", "uk"))


@pytest.mark.asyncio
async def test_remind_remove_existing_time():
    reminder.add_time("09:00")
    message = make_message(OWNER_ID, "/remind remove 9:00")
    await h.cmd_remind(message)
    assert "09:00" not in reminder.get_times()
    message.answer.assert_awaited_once_with(t("remind_time_removed", "uk"))


@pytest.mark.asyncio
async def test_remind_remove_unknown_time():
    message = make_message(OWNER_ID, "/remind remove 07:00")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("remind_time_not_found", "uk"))


@pytest.mark.asyncio
async def test_remind_remove_garbage_argument_is_not_found():
    message = make_message(OWNER_ID, "/remind del abc")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("remind_time_not_found", "uk"))


@pytest.mark.asyncio
async def test_remind_unknown_subcommand_shows_format_hint():
    message = make_message(OWNER_ID, "/remind banana")
    await h.cmd_remind(message)
    message.answer.assert_awaited_once_with(t("remind_format_hint", "uk"))


# --- inline buttons ---

@pytest.mark.asyncio
async def test_button_on_enables_and_refreshes_menu():
    reminder.set_enabled(False)
    callback = make_callback(OWNER_ID, "remind_on")
    await h.cb_remind_on(callback)
    assert reminder.is_enabled() is True
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_button_off_disables_and_refreshes_menu():
    callback = make_callback(OWNER_ID, "remind_off")
    await h.cb_remind_off(callback)
    assert reminder.is_enabled() is False
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_button_add_time_starts_waiting_for_text():
    callback = make_callback(OWNER_ID, "remind_add_time")
    await h.cb_remind_add_time(callback)
    assert _shared.awaiting_remind_time[OWNER_ID] is True
    callback.message.edit_text.assert_awaited_once_with(t("remind_time_prompt", "uk"))


@pytest.mark.asyncio
async def test_button_add_time_clears_other_pending_flows():
    _shared.awaiting_find_query[OWNER_ID] = True
    await h.cb_remind_add_time(make_callback(OWNER_ID, "remind_add_time"))
    assert OWNER_ID not in _shared.awaiting_find_query


@pytest.mark.asyncio
async def test_button_remove_time_lists_each_time():
    reminder.add_time("09:00")
    callback = make_callback(OWNER_ID, "remind_remove_time")
    await h.cb_remind_remove_time(callback)
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert keyboard_callback_data(markup) == ["remind_del_time:09:00", "remind_del_time:21:00"]


@pytest.mark.asyncio
async def test_button_delete_time_removes_it():
    reminder.add_time("09:00")
    callback = make_callback(OWNER_ID, "remind_del_time:09:00")
    await h.cb_remind_delete_time(callback)
    assert "09:00" not in reminder.get_times()
    callback.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_nav_remind_sends_status_message():
    callback = make_callback(OWNER_ID, "nav:remind")
    await h.cb_nav_remind(callback)
    assert t("remind_status_on", "uk") in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,data", [
    ("cb_remind_on", "remind_on"),
    ("cb_remind_off", "remind_off"),
    ("cb_remind_add_time", "remind_add_time"),
    ("cb_remind_remove_time", "remind_remove_time"),
    ("cb_remind_delete_time", "remind_del_time:21:00"),
    ("cb_nav_remind", "nav:remind"),
])
async def test_every_button_is_denied_for_strangers(handler, data):
    callback = make_callback(STRANGER_ID, data)
    await getattr(h, handler)(callback)
    assert _denied(callback)
    callback.message.edit_text.assert_not_awaited()
    callback.message.answer.assert_not_awaited()
    assert reminder.is_enabled() is True and reminder.get_times() == ["21:00"]
