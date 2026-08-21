import pytest

from core.handlers import _shared


@pytest.fixture(autouse=True)
def clean_awaiting_state():
    """Every test starts and ends with no leftover awaiting_* state for
    the test user IDs, so tests can't leak into each other."""
    test_ids = [111, 222, 999]
    for uid in test_ids:
        _shared.clear_awaiting_states(uid)
    yield
    for uid in test_ids:
        _shared.clear_awaiting_states(uid)


def test_clear_awaiting_states_returns_false_when_nothing_pending():
    assert _shared.clear_awaiting_states(111) is False


def test_clear_awaiting_states_returns_true_when_something_pending():
    _shared.awaiting_find_query[111] = True
    assert _shared.clear_awaiting_states(111) is True


def test_clear_awaiting_states_clears_all_dicts():
    _shared.awaiting_category_text[111] = "entry_a"
    _shared.awaiting_edit_field[111] = ("edit_a", "amount")
    _shared.awaiting_report_args[111] = True
    _shared.awaiting_report_topn[111] = "week"
    _shared.awaiting_budget_amount[111] = "Кафе"
    _shared.awaiting_budget_category[111] = True
    _shared.awaiting_remind_time[111] = True
    _shared.awaiting_find_query[111] = True

    _shared.clear_awaiting_states(111)

    assert 111 not in _shared.awaiting_category_text
    assert 111 not in _shared.awaiting_edit_field
    assert 111 not in _shared.awaiting_report_args
    assert 111 not in _shared.awaiting_report_topn
    assert 111 not in _shared.awaiting_budget_amount
    assert 111 not in _shared.awaiting_budget_category
    assert 111 not in _shared.awaiting_remind_time
    assert 111 not in _shared.awaiting_find_query


def test_clear_awaiting_states_does_not_affect_other_users():
    _shared.awaiting_find_query[111] = True
    _shared.awaiting_find_query[222] = True

    _shared.clear_awaiting_states(111)

    assert 111 not in _shared.awaiting_find_query
    assert 222 in _shared.awaiting_find_query  # untouched
    _shared.clear_awaiting_states(222)  # cleanup


def test_starting_new_flow_clears_stale_state_from_abandoned_one():
    # Simulates: user tapped "Edit category" in /edit (state set), then
    # without answering, tapped "Add budget limit -> custom category"
    # in /budget — the second flow's setup must not leave the first
    # one's stale state around to hijack a later message.
    _shared.awaiting_edit_field[999] = ("edit_x", "category")

    _shared.clear_awaiting_states(999)
    _shared.awaiting_budget_category[999] = True

    assert 999 not in _shared.awaiting_edit_field
    assert _shared.awaiting_budget_category.get(999) is True