import pytest

from core import language


@pytest.fixture(autouse=True)
def isolate_settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(language, "_SETTINGS_PATH", str(tmp_path / "language_settings.json"))
    language._state.clear()
    yield


def test_default_language_is_ukrainian():
    assert language.get_language(12345) == "uk"


def test_set_and_get_language():
    language.set_language(12345, "en")
    assert language.get_language(12345) == "en"


def test_set_language_rejects_unsupported():
    with pytest.raises(ValueError):
        language.set_language(12345, "fr")


def test_language_persists_across_reload():
    language.set_language(999, "en")
    language._state.clear()
    language._load_settings()
    assert language.get_language(999) == "en"


def test_different_users_independent():
    language.set_language(1, "en")
    language.set_language(2, "uk")
    assert language.get_language(1) == "en"
    assert language.get_language(2) == "uk"