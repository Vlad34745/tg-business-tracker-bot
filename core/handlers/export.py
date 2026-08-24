"""/export command: dump all transactions as a CSV file."""
from datetime import datetime
from aiogram import F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from core.export import build_csv
from core import language
from core.i18n import t
from core.storage import get_all_transactions
from core.handlers._shared import router, is_owner

@router.message(Command("export"))
async def cmd_export(message: Message):
    lang = language.get_language(message.from_user.id)
    if not is_owner(message.from_user.id):
        await message.answer(t("access_denied", lang))
        return

    try:
        rows = await get_all_transactions(message.from_user.id)
    except Exception as e:
        await message.answer(t("err_sheet_read", lang, e=e))
        return

    if not rows:
        await message.answer(t("no_entries_yet", lang))
        return

    csv_text = build_csv(rows)
    # utf-8-sig BOM so Excel opens Cyrillic text correctly by default
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    await message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=t("export_caption", lang, n=len(rows))
    )

@router.callback_query(F.data == "nav:export")
async def cb_nav_export(callback: CallbackQuery):
    lang = language.get_language(callback.from_user.id)
    if not is_owner(callback.from_user.id):
        await callback.answer(t("access_denied", lang), show_alert=True)
        return
    await callback.answer()

    try:
        rows = await get_all_transactions(callback.from_user.id)
    except Exception as e:
        await callback.message.answer(t("err_sheet_read", lang, e=e))
        return

    if not rows:
        await callback.message.answer(t("no_entries_yet", lang))
        return

    csv_text = build_csv(rows)
    file_bytes = csv_text.encode("utf-8-sig")
    filename = f"transactions_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    await callback.message.answer_document(
        BufferedInputFile(file_bytes, filename=filename),
        caption=t("export_caption", lang, n=len(rows))
    )

