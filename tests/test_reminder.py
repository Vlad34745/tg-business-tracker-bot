import pytest

from core import reminder


@pytest.fixture(autouse=True)
def isolate_settings_file(tmp_path, monkeypatch):
    """Redirect reminder settings to a throwaway file so tests never
    touch (or depend on) the real reminder_settings.json on disk."""
    monkeypatch.setattr(reminder, "_SETTINGS_PATH", str(tmp_path / "reminder_settings.json"))
    reminder._state["enabled"] = True
    reminder._state["times"] = ["21:00"]
    yield


def test_reminder_default_enabled():
    assert reminder.is_enabled() is True


def test_reminder_can_be_disabled():
    reminder.set_enabled(False)
    assert reminder.is_enabled() is False
    reminder.set_enabled(True)
    assert reminder.is_enabled() is True


def test_reminder_state_persists_across_reload():
    reminder.set_enabled(False)
    reminder.add_time("09:00")
    # Simulate a bot restart: reset in-memory state, then reload from disk.
    reminder._state["enabled"] = True
    reminder._state["times"] = ["21:00"]
    reminder._load_settings()
    assert reminder.is_enabled() is False
    assert "09:00" in reminder.get_times()


def test_is_valid_time():
    assert reminder.is_valid_time("21:00") is True
    assert reminder.is_valid_time("9:5") is True
    assert reminder.is_valid_time("24:00") is False
    assert reminder.is_valid_time("12:60") is False
    assert reminder.is_valid_time("not a time") is False


def test_normalize_time():
    assert reminder.normalize_time("9:5") == "09:05"
    assert reminder.normalize_time("21:00") == "21:00"


def test_add_time_avoids_duplicates():
    added_first = reminder.add_time("09:00")
    added_second = reminder.add_time("09:00")
    assert added_first is True
    assert added_second is False
    assert reminder.get_times().count("09:00") == 1


def test_add_time_supports_multiple():
    reminder.add_time("09:00")
    reminder.add_time("14:30")
    times = reminder.get_times()
    assert "09:00" in times
    assert "14:30" in times
    assert "21:00" in times  # default kept
    assert len(times) == 3


def test_remove_time():
    reminder.add_time("09:00")
    removed = reminder.remove_time("09:00")
    assert removed is True
    assert "09:00" not in reminder.get_times()


def test_remove_nonexistent_time_returns_false():
    assert reminder.remove_time("03:33") is False


def test_remove_last_time_falls_back_to_default():
    # Only "21:00" is configured (the fixture default) — removing it
    # should NOT leave zero times configured.
    reminder.remove_time("21:00")
    assert reminder.get_times() == ["21:00"]


def test_get_times_sorted():
    reminder.add_time("23:00")
    reminder.add_time("06:00")
    assert reminder.get_times() == sorted(reminder.get_times())