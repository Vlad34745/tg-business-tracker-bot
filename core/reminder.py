import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Module-level state, accessed via functions (not a plain import of the
# variable) so toggling it from handlers.py actually affects the running
# background loop in bot.py.
_state = {"enabled": True}


def is_enabled() -> bool:
    return _state["enabled"]


def set_enabled(value: bool) -> None:
    _state["enabled"] = value


async def reminder_loop(bot, user_ids: list, hour: int = 21, minute: int = 0):
    """
    Background task: sleeps until the next occurrence of hour:minute
    (local time) each day and, if reminders are enabled, sends a
    "log your expenses" nudge to every allowed user.
    """
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())

        if is_enabled():
            for user_id in user_ids:
                try:
                    await bot.send_message(
                        int(user_id),
                        "🔔 <b>Нагадування:</b> не забудь записати сьогоднішні витрати!",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.warning(f"Failed to send reminder to {user_id}: {e}")

        # Avoid re-triggering within the same minute due to loop timing.
        await asyncio.sleep(60)