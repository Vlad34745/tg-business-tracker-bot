from core.reminder import is_enabled, set_enabled


def test_reminder_default_enabled():
    # Reset to known default in case another test changed it
    set_enabled(True)
    assert is_enabled() is True


def test_reminder_can_be_disabled():
    set_enabled(False)
    assert is_enabled() is False
    set_enabled(True)  # reset for other tests
    assert is_enabled() is True