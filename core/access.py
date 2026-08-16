"""
Auto-open access: anyone who presses /start gets added to the allowed
list automatically instead of needing their Telegram user ID added to
ALLOWED_USER_ID by hand. Per-user data isolation (see core/sheets.py)
means a self-registered user only ever touches their own sheet tab,
so opening access this way doesn't expose anyone else's data — the
main risk is just more people (or spam) hitting the bot and, in turn,
the Google Sheets API quota.

IDs from .env's ALLOWED_USER_ID are always allowed and never need to
self-register. Self-registered IDs persist to auto_users.json so the
list survives a bot restart.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "auto_users.json"
)

# Self-registered user IDs (str), separate from the static
# ALLOWED_USER_ID env list.
_auto_users: set = set()


def _load_state() -> None:
    try:
        with open(_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _auto_users.update(str(uid) for uid in data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # no saved state yet


def _save_state() -> None:
    try:
        with open(_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(_auto_users), f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Failed to save auto-registered users: {e}")


_load_state()


def register(user_id: int) -> bool:
    """
    Registers a user as self-signed-up. Returns True if this was a
    new registration, False if they were already registered (so the
    caller can tell a first-time /start from a returning one).
    """
    uid = str(user_id)
    if uid in _auto_users:
        return False
    _auto_users.add(uid)
    _save_state()
    return True


def count() -> int:
    """Number of self-registered (auto) users."""
    return len(_auto_users)


def is_registered(user_id: int) -> bool:
    return str(user_id) in _auto_users