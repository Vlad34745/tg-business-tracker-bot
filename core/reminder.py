import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from core import language
from core.i18n import t

logger = logging.getLogger(__name__)

# Settings persist to a small JSON file next to the project root, so the
# enabled/disabled flag and configured times survive bot restarts —
# previously this was in-memory only and silently reset to the default
# (enabled, 21:00) every time the bot process restarted.
_SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reminder_settings.json"
)
_DEFAULT_TIMES = ["21:00"]

_state = {"enabled": True, "times": list(_DEFAULT_TIMES)}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]?\d)$")


def _load_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("enabled"), bool):
            _state["enabled"] = data["enabled"]
        if isinstance(data.get("times"), list) and data["times"]:
            _state["times"] = data["times"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass  # no saved settings yet — keep the defaults


def _save_settings() -> None:
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
    except OSError as e:
        logger.warning(f"Failed to save reminder settings: {e}")


_load_settings()  # restore persisted state as soon as this module is imported


def is_enabled() -> bool:
    return _state["enabled"]


def set_enabled(value: bool) -> None:
    _state["enabled"] = value
    _save_settings()


def get_times() -> list:
    """Returns the configured reminder times as sorted 'HH:MM' strings."""
    return sorted(_state["times"])


def is_valid_time(time_str: str) -> bool:
    return bool(_TIME_RE.fullmatch(time_str.strip()))


def normalize_time(time_str: str) -> str:
    """'9:5' -> '09:05'. Assumes is_valid_time() already passed."""
    hour, minute = time_str.strip().split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


def add_time(time_str: str) -> bool:
    """Add a reminder time if not already present. Returns True if added."""
    normalized = normalize_time(time_str)
    if normalized in _state["times"]:
        return False
    _state["times"].append(normalized)
    _save_settings()
    return True


def remove_time(time_str: str) -> bool:
    """Remove a reminder time. Refuses to leave zero configured times —
    falls back to the default instead, so reminders can't end up with
    no scheduled times while still marked enabled."""
    if time_str not in _state["times"]:
        return False
    if len(_state["times"]) == 1:
        _state["times"] = list(_DEFAULT_TIMES)
    else:
        _state["times"].remove(time_str)
    _save_settings()
    return True


async def reminder_loop(bot, user_ids: list):
    """
    Background task: sleeps until the next occurrence of any configured
    reminder time (local time) and, if reminders are enabled, sends a
    "log your expenses" nudge to every allowed user. Supports multiple
    times per day — re-reads the configured times on every cycle, so
    changes made via /remind take effect without a bot restart.
    """
    while True:
        now = datetime.now()
        times = get_times()

        candidates = []
        for time_str in times:
            hour, minute = map(int, time_str.split(":"))
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            candidates.append(target)

        next_target = min(candidates) if candidates else now + timedelta(hours=1)
        await asyncio.sleep((next_target - now).total_seconds())

        if is_enabled():
            for user_id in user_ids:
                try:
                    lang = language.get_language(int(user_id))
                    await bot.send_message(
                        int(user_id),
                        t("daily_reminder_text", lang),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send reminder to {user_id}: {e}")

        # Avoid re-triggering within the same minute due to loop timing.
        await asyncio.sleep(60)