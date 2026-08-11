from core.i18n import t


def test_t_returns_ukrainian_by_default_key():
    assert t("btn_save", "uk") == "✅ Зберегти"


def test_t_returns_english():
    assert t("btn_save", "en") == "✅ Save"


def test_t_formats_placeholders():
    assert t("btn_save_all", "uk", n=3) == "✅ Зберегти всі (3)"
    assert t("btn_save_all", "en", n=3) == "✅ Save all (3)"


def test_t_falls_back_to_ukrainian_for_unknown_language():
    assert t("btn_save", "fr") == "✅ Зберегти"


def test_t_returns_key_for_unknown_key():
    assert t("nonexistent_key", "uk") == "nonexistent_key"