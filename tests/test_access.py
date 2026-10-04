import json

from core import access


def test_register_new_user_returns_true():
    assert access.register(555) is True
    assert access.is_registered(555) is True


def test_register_same_user_twice_returns_false():
    access.register(555)
    assert access.register(555) is False
    assert access.count() == 1


def test_is_registered_false_for_unknown_user():
    assert access.is_registered(42) is False


def test_count_and_all_ids():
    access.register(1)
    access.register(2)
    assert access.count() == 2
    assert access.all_ids() == {"1", "2"}


def test_all_ids_returns_a_copy():
    access.register(1)
    ids = access.all_ids()
    ids.add("999")
    assert access.is_registered(999) is False


def test_register_persists_to_disk():
    access.register(777)
    with open(access._STATE_PATH, encoding="utf-8") as f:
        assert json.load(f) == ["777"]


def test_state_survives_reload():
    access.register(10)
    access.register(20)
    access._auto_users.clear()  # simulate a bot restart
    access._load_state()
    assert access.is_registered(10) and access.is_registered(20)


def test_load_state_with_missing_file_is_a_noop():
    access._load_state()
    assert access.count() == 0


def test_load_state_ignores_invalid_json():
    with open(access._STATE_PATH, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    access._load_state()
    assert access.count() == 0


def test_load_state_ignores_non_list_json():
    with open(access._STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"a": 1}, f)
    access._load_state()
    assert access.count() == 0


def test_save_state_failure_is_logged_not_raised(tmp_path, monkeypatch, caplog):
    # Pointing the state path at a directory makes open(..., "w") fail with an OSError.
    monkeypatch.setattr(access, "_STATE_PATH", str(tmp_path))
    with caplog.at_level("WARNING"):
        assert access.register(5) is True  # still registered in memory
    assert access.is_registered(5) is True
    assert "Failed to save" in caplog.text
