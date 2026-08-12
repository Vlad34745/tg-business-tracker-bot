"""/start welcome message and /language switching."""
from aiogram import F
from aiogram.types import (
    Message, ReplyKeyboardRemove, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command
from core import language
from core.i18n import t
from core.handlers._shared import router, is_owner, _quick_menu_keyboard

@router.message(CommandStart())
async def cmd_start(message: Message):
    lang = language.get_language(message.from_user.id)

    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied_start", lang))
        return

    if lang == "en":
        welcome_text = (
            "<b>Hi! I'm your personal Finance Tracker 📊</b>\n\n"
            "I can instantly log your income and expenses into a Google Sheet.\n\n"
            "✏️ <b>How to send entries:</b>\n"
            "• <code>150 Lunch</code>\n"
            "• <code>25000 Salary June</code>\n"
            "• <code>Taxi 220 downtown</code>\n\n"
            "Before saving, I'll show a preview with ✅/❌ buttons.\n"
            "If a similar entry was just saved, I'll flag it separately.\n\n"
            "🔎 <code>/last</code> — most recent entry.\n"
            "🗑️ <code>/undo</code> — delete the last entry (or the whole batch, if you saved several together).\n"
            "📊 <code>/report</code> — report: pick period and category count with buttons.\n"
            "💼 <code>/budget</code> — category limits: view/add/remove with buttons.\n"
            "📄 <code>/export</code> — all entries as CSV.\n"
            "🔍 <code>/find</code> — search: pick a category or enter your own text.\n"
            "🔔 <code>/remind</code> — reminders with buttons: on/off, add or remove a time.\n"
            "🌐 <code>/language</code> — switch language.\n\n"
            "✨ You can send several transactions in one message — one\n"
            "   per line — and I'll offer to save them all together.\n\n"
            "Try sending me any transaction!"
        )
    else:
        welcome_text = (
            "<b>Привіт! Я твій особистий Фінансовий Трекер 📊</b>\n\n"
            "Я вмію миттєво записувати твої доходи та витрати у Google Таблицю.\n\n"
            "✏️ <b>Як відправляти записи:</b>\n"
            "• <code>150 Обіди</code>\n"
            "• <code>25000 Зарплата червень</code>\n"
            "• <code>Таксі 220 центр</code>\n\n"
            "Перед збереженням я покажу перевірку з кнопками ✅/❌.\n"
            "Якщо схожий запис уже був нещодавно — попереджу окремо.\n\n"
            "🔎 <code>/last</code> — останній запис.\n"
            "🗑️ <code>/undo</code> — видалити останній запис (або весь пакет, якщо зберігав кілька разом).\n"
            "📊 <code>/report</code> — звіт: оберу період і кількість категорій кнопками.\n"
            "💼 <code>/budget</code> — ліміти по категоріях: перегляд/додавання/видалення кнопками.\n"
            "📄 <code>/export</code> — усі записи у CSV.\n"
            "🔍 <code>/find</code> — пошук: обери категорію чи «Ввести текст» кнопкою.\n"
            "🔔 <code>/remind</code> — нагадування кнопками: увімкнути/вимкнути, додати чи прибрати час.\n"
            "🌐 <code>/language</code> — зміна мови.\n\n"
            "✨ Можна надіслати кілька транзакцій одним повідомленням —\n"
            "   кожну з нового рядка, і я запропоную зберегти всі одразу.\n\n"
            "Спробуй відправити мені будь-яку транзакцію!"
        )
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await message.answer(welcome_text, reply_markup=_quick_menu_keyboard(lang))

@router.message(Command("language"))
async def cmd_language(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", language.get_language(message.from_user.id)))
        return

    lang = language.get_language(message.from_user.id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_set:uk"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_set:en"),
    ]])
    await message.answer(t("language_prompt", lang), reply_markup=keyboard)

@router.callback_query(F.data.startswith("lang_set:"))
async def cb_set_language(callback: CallbackQuery):
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", language.get_language(callback.from_user.id)), show_alert=True)
        return

    new_lang = callback.data.split(":", 1)[1]
    language.set_language(callback.from_user.id, new_lang)
    await language.apply_commands_for_chat(callback.bot, callback.from_user.id)
    await callback.answer()
    await callback.message.edit_text(t("language_set", new_lang))

@router.callback_query(F.data == "nav:language")
async def cb_nav_language(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇺🇦 Українська", callback_data="lang_set:uk"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_set:en"),
    ]])
    await callback.message.answer(t("language_prompt", lang), reply_markup=keyboard)