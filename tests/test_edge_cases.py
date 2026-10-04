"""Small gaps in otherwise well-covered modules: error branches and fallbacks."""
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock
from googleapiclient.errors import HttpError

import core.storage as storage
from core import language
from core.handlers import entries as e
from core.handlers import _shared
from core.i18n import t
from core.report import (
    _parse_date, _to_float, compute_change_pct, get_frequent_categories, previous_month,
)
from core.validator import _capitalize_first, normalize_category
from tests.helpers import OWNER_ID, make_callback


def _http_error(status: int) -> HttpError:
    resp = Mock()
    resp.status = status
    return HttpError(resp, b"error body")


# --- core.report ---

def test_parse_date_handles_empty_and_invalid_values():
    assert _parse_date("") is None
    assert _parse_date("not a date") is None
    assert _parse_date(" 25.07.2026 ").day == 25
    assert _parse_date("2026-07-25").month == 7


def test_to_float_handles_text_and_garbage():
    assert _to_float("12,5") == 12.5
    assert _to_float("abc") == 0.0
    assert _to_float(None) == 0.0
    assert _to_float(7) == 7.0


def test_previous_month_wraps_around_january():
    assert previous_month(2026, 1) == (2025, 12)
    assert previous_month(2026, 5) == (2026, 4)


def test_change_pct_is_none_when_previous_is_zero():
    assert compute_change_pct(100, 0) is None
    assert compute_change_pct(150, 100) == pytest.approx(50.0)


def test_frequent_categories_skip_rows_with_empty_category():
    rows = [["d", "Expense", "", 1, "-"], ["d", "Expense", "Кафе", 1, "-"], ["d", "Expense"]]
    assert get_frequent_categories(rows) == ["Кафе"]


# --- core.validator ---

def test_capitalize_first_keeps_empty_strings_and_acronyms():
    assert _capitalize_first("") == ""
    assert _capitalize_first("атб") == "Атб"
    assert _capitalize_first("АТБ") == "АТБ"
    assert _capitalize_first("iPhone") == "IPhone"


def test_normalize_category_of_blank_text_is_empty():
    assert normalize_category("   ") == ""


# --- core.i18n ---

def test_t_returns_the_key_when_missing_and_falls_back_to_ukrainian():
    assert t("no_such_key", "uk") == "no_such_key"
    assert t("access_denied", "de") == t("access_denied", "uk")


def test_t_with_wrong_format_arguments_returns_the_unformatted_text():
    assert "{time}" in t("remind_time_added", "uk", wrong_name=1)


# --- core.language ---

def test_save_settings_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(language, "_SETTINGS_PATH", str(tmp_path))  # a directory -> OSError on open()
    with caplog.at_level("WARNING"):
        language.set_language(1, "en")
    assert language.get_language(1) == "en"  # still applied in memory
    assert "Failed to save language settings" in caplog.text


def test_load_settings_ignores_unsupported_languages(tmp_path):
    import json
    with open(language._SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"1": "en", "2": "fr"}, f)
    language._load_settings()
    assert language.get_language(1) == "en"
    assert language.get_language(2) == "uk"


@pytest.mark.asyncio
async def test_apply_commands_failure_is_logged_not_raised(caplog):
    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("telegram down"))
    with caplog.at_level("WARNING"):
        await language.apply_commands_for_chat(bot, 42)
    assert caplog.text != ""


@pytest.mark.asyncio
async def test_apply_commands_uses_the_users_language():
    language.set_language(42, "en")
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()
    await language.apply_commands_for_chat(bot, 42)
    assert bot.set_my_commands.await_args.args[0] is language.COMMANDS_EN


def test_get_commands_defaults_to_ukrainian():
    assert language.get_commands("uk") is language.COMMANDS_UK
    assert language.get_commands("xx") is language.COMMANDS_UK


# --- core.storage: errors that must NOT be swallowed ---

@pytest.mark.asyncio
async def test_append_transaction_reraises_permission_errors(sheets_service):
    from core.storage import _client
    _client._known_existing_tabs.add("Transactions")
    sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await storage.append_transaction(111, "01.08.2026", "Expense", "Кафе", 1, "-")


@pytest.mark.asyncio
async def test_append_transactions_batch_reraises_permission_errors(sheets_service):
    from core.storage import _client
    _client._known_existing_tabs.add("Transactions")
    sheets_service.spreadsheets.return_value.values.return_value.append.return_value.execute.side_effect = _http_error(403)
    entry = {"date": "d", "type_tr": "Expense", "category": "Кафе", "amount": 1, "description": "-"}
    with pytest.raises(HttpError):
        await storage.append_transactions_batch(111, [entry])


# --- /undo (nav button) with an unparseable amount in a batch ---

@pytest.mark.asyncio
async def test_nav_undo_batch_ignores_unparseable_amounts(monkeypatch):
    rows = [["d", "Expense", "Кафе", "n/a", "x"], ["d", "Expense", "Таксі", 50, "x"]]
    monkeypatch.setattr(e, "get_last_n_transactions", AsyncMock(return_value=rows))
    _shared._last_action_count[OWNER_ID] = 2
    try:
        callback = make_callback(OWNER_ID, "nav:undo")
        await e.cb_nav_undo(callback)
    finally:
        _shared._last_action_count.clear()
    assert t("total_label", "uk", total=50.0) in callback.message.answer.await_args.args[0]
