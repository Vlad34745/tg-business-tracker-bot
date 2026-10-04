"""
Shared test fixtures.

The autouse fixture below keeps every test hermetic:
- the JSON "settings" files (language / reminder / self-registered users)
  are redirected into pytest's tmp_path, so tests never read or write the
  real *.json files in the project root;
- the in-memory state of those modules is snapshotted and restored, so one
  test can't leak state into the next;
- the owner list (ALLOWED_USER_ID) is pinned to a single known id ("123")
  regardless of what the developer has in their local .env file.
"""
import pytest
from unittest.mock import MagicMock

from core import access, language, reminder
from core.handlers import _shared
from core.storage import _client

from tests.helpers import OWNER_ID


@pytest.fixture(autouse=True)
def hermetic_state(tmp_path, monkeypatch):
    monkeypatch.setattr(language, "_SETTINGS_PATH", str(tmp_path / "language_settings.json"))
    monkeypatch.setattr(reminder, "_SETTINGS_PATH", str(tmp_path / "reminder_settings.json"))
    monkeypatch.setattr(access, "_STATE_PATH", str(tmp_path / "auto_users.json"))

    saved_language = dict(language._state)
    saved_reminder = {"enabled": reminder._state["enabled"], "times": list(reminder._state["times"])}
    saved_auto_users = set(access._auto_users)
    saved_owner_ids = list(_shared.ALLOWED_IDS)

    language._state.clear()
    reminder._state["enabled"] = True
    reminder._state["times"] = ["21:00"]
    access._auto_users.clear()
    _shared.ALLOWED_IDS[:] = [str(OWNER_ID)]

    yield

    language._state.clear()
    language._state.update(saved_language)
    reminder._state["enabled"] = saved_reminder["enabled"]
    reminder._state["times"] = saved_reminder["times"]
    access._auto_users.clear()
    access._auto_users.update(saved_auto_users)
    _shared.ALLOWED_IDS[:] = saved_owner_ids


@pytest.fixture
def sheets_service(monkeypatch):
    """A MagicMock standing in for the Google Sheets service, installed as
    the module's cached client so no credentials / network are needed."""
    service = MagicMock()
    monkeypatch.setattr(_client, "_service_cache", service)
    monkeypatch.setattr(_client, "ALLOWED_IDS", ["111", "222"])
    monkeypatch.setattr(_client.time, "sleep", lambda seconds: None)
    _client._known_existing_tabs.clear()
    yield service
    _client._known_existing_tabs.clear()
