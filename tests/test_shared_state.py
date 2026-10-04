from core.handlers import _shared


def _clear():
    _shared.pending_entries.clear()
    _shared.pending_batches.clear()
    _shared.pending_edits.clear()


def test_pending_entries_evict_the_oldest_beyond_the_cap():
    _clear()
    try:
        first_id = _shared._store_pending_entry({"n": 0})
        for n in range(1, _shared._PENDING_ENTRIES_MAX + 1):
            _shared._store_pending_entry({"n": n})
        assert len(_shared.pending_entries) == _shared._PENDING_ENTRIES_MAX
        assert first_id not in _shared.pending_entries
    finally:
        _clear()


def test_pending_batches_evict_the_oldest_beyond_the_cap():
    _clear()
    try:
        first_id = _shared._store_pending_batch([{"n": 0}])
        for n in range(1, _shared._PENDING_BATCHES_MAX + 1):
            _shared._store_pending_batch([{"n": n}])
        assert len(_shared.pending_batches) == _shared._PENDING_BATCHES_MAX
        assert first_id not in _shared.pending_batches
    finally:
        _clear()


def test_pending_edits_evict_the_oldest_beyond_the_cap():
    _clear()
    try:
        first_id = _shared._store_pending_edit({"n": 0})
        for n in range(1, _shared._PENDING_EDITS_MAX + 1):
            _shared._store_pending_edit({"n": n})
        assert len(_shared.pending_edits) == _shared._PENDING_EDITS_MAX
        assert first_id not in _shared.pending_edits
    finally:
        _clear()


def test_raw_rows_equal_compares_non_numeric_amounts_as_text():
    a = ["01.08.2026", "Expense", "Кафе", "n/a", "x"]
    assert _shared._raw_rows_equal(a, list(a)) is True
    assert _shared._raw_rows_equal(a, ["01.08.2026", "Expense", "Кафе", "other", "x"]) is False


def test_format_transaction_pads_and_truncates():
    assert _shared._format_transaction(["d", "Expense"]) == ("d", "Expense", "-", "-", "-")
    assert _shared._format_transaction(list("abcdefg")) == tuple("abcde")


def test_get_category_choice_rejects_negative_and_missing_indexes():
    _shared._store_category_choices(1, ["A", "B"])
    try:
        assert _shared._get_category_choice(1, 1) == "B"
        assert _shared._get_category_choice(1, -1) is None
        assert _shared._get_category_choice(2, 0) is None
    finally:
        _shared._category_choices_cache.clear()


def test_is_owner_accepts_listed_and_self_registered_users():
    from core import access
    assert _shared.is_owner(123) is True
    assert _shared.is_owner(999) is False
    access.register(999)
    assert _shared.is_owner(999) is True
