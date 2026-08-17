"""/remind command: daily reminder on/off and configured times."""
from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from core import reminder
from core import language
from core.i18n import t
from core.handlers._shared import router, is_owner, awaiting_remind_time

def _remind_status_text(lang: str = "uk") -> str:
    status = t("remind_status_on", lang) if reminder.is_enabled() else t("remind_status_off", lang)
    times = ", ".join(reminder.get_times())
    return t("remind_status_line", lang, status=status, times=times)

def _remind_menu_keyboard(lang: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t("btn_remind_on", lang), callback_data="remind_on"),
            InlineKeyboardButton(text=t("btn_remind_off", lang), callback_data="remind_off"),
        ],
        [
            InlineKeyboardButton(text=t("btn_add_time", lang), callback_data="remind_add_time"),
            InlineKeyboardButton(text=t("btn_remove_time", lang), callback_data="remind_remove_time"),
        ],
    ])

@router.message(Command("remind"))
async def cmd_remind(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    args = message.text.split()[1:]
    if not args:
        await message.answer(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))
        return

    sub = args[0].lower()
    if sub in ("on", "увімкнути"):
        reminder.set_enabled(True)
        await message.answer(t("remind_on_msg", lang))
    elif sub in ("off", "вимкнути"):
        reminder.set_enabled(False)
        await message.answer(t("remind_off_msg", lang))
    elif sub in ("add",) and len(args) >= 2 and reminder.is_valid_time(args[1]):
        added = reminder.add_time(args[1])
        norm = reminder.normalize_time(args[1])
        msg = t("remind_time_added", lang, time=norm) if added else t("remind_time_exists", lang, time=norm)
        await message.answer(msg)
    elif sub in ("remove", "del") and len(args) >= 2:
        removed = reminder.remove_time(reminder.normalize_time(args[1]) if reminder.is_valid_time(args[1]) else args[1])
        msg = t("remind_time_removed", lang) if removed else t("remind_time_not_found", lang)
        await message.answer(msg)
    else:
        await message.answer(t("remind_format_hint", lang))

@router.callback_query(F.data == "remind_on")
async def cb_remind_on(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    reminder.set_enabled(True)
    await callback.answer(t("toast_turned_on", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data == "remind_off")
async def cb_remind_off(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    reminder.set_enabled(False)
    await callback.answer(t("toast_turned_off", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data == "remind_add_time")
async def cb_remind_add_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    awaiting_remind_time[callback.from_user.id] = True
    await callback.answer()
    await callback.message.edit_text(t("remind_time_prompt", lang))

@router.callback_query(F.data == "remind_remove_time")
async def cb_remind_remove_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    times = reminder.get_times()
    buttons = [
        [InlineKeyboardButton(text=time_str, callback_data=f"remind_del_time:{time_str}")]
        for time_str in times
    ]
    await callback.message.edit_text(t("remind_which_to_remove", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@router.callback_query(F.data.startswith("remind_del_time:"))
async def cb_remind_delete_time(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    time_str = callback.data.split(":", 1)[1]
    reminder.remove_time(time_str)
    await callback.answer(t("toast_deleted", lang))
    await callback.message.edit_text(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

@router.callback_query(F.data == "nav:remind")
async def cb_nav_remind(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(_remind_status_text(lang), reply_markup=_remind_menu_keyboard(lang))

