"""
Handlers package: splits what used to be a single 1600-line
core/handlers.py into one module per command/domain, all registering
on the same shared Router from _shared.py.

Import order matters here: entries.py calls functions defined in
reports.py, find.py, remind.py, and edit.py (to resume those flows
from free-text input), so those four must be imported before
entries.py. Every submodule is imported purely for its side effect of
registering handlers on `router` — nothing else needs to be
re-exported except what core/bot.py and tests import from this
package.
"""
from core.handlers._shared import router, ALLOWED_IDS, is_owner

from core.handlers import start        # noqa: F401  (registers /start, /language)
from core.handlers import reports      # noqa: F401  (registers /report)
from core.handlers import budget       # noqa: F401  (registers /budget)
from core.handlers import export       # noqa: F401  (registers /export)
from core.handlers import find         # noqa: F401  (registers /find)
from core.handlers import remind       # noqa: F401  (registers /remind)
from core.handlers import edit         # noqa: F401  (registers /edit)
from core.handlers import entries      # noqa: F401  (registers /last, /undo, and the catch-all text handler — must be last)

__all__ = ["router", "ALLOWED_IDS", "is_owner"]
