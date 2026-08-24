"""
Shared low-level plumbing used by both core.storage.transactions and
core.storage.budgets: the cached Sheets API client, retry-with-backoff
logic for transient failures, per-user tab name resolution, and a
process-lifetime cache of which tabs are confirmed to exist.

Split out of what used to be a single 773-line core/sheets.py so each
concern (client setup, transactions, budgets) lives in its own module.
"""
import os
import time
import random
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dotenv import load_dotenv

load_dotenv()

# Path to the Google Service Account credentials file
CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# API access scope for Google Sheets
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Retry policy for transient Google Sheets API failures — rate limits
# and momentary server/network hiccups shouldn't surface as an error
# to the user if a short retry would have succeeded. Non-transient
# errors (bad request, auth failure) are never retried. Note this
# retries the *entire* sync_worker, including any writes inside it —
# if a write actually succeeded server-side but the response was lost
# before we saw it, a retry could in rare cases duplicate that write.
# This is an accepted trade-off: silently failing on a blip is worse
# for a single-user finance tracker than a rare duplicate row, which
# is easy to spot and fix with /undo.
_TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_DELAY_SECONDS = 1.0


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, HttpError):
        return exc.resp.status in _TRANSIENT_STATUS_CODES
    return isinstance(exc, (ConnectionError, TimeoutError))


def _retry_call(fn):
    """
    Runs a synchronous Sheets API call with exponential backoff (plus
    jitter) on transient errors. Raises immediately for non-transient
    errors or once retries are exhausted.
    """
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn()
        except Exception as e:
            if not _is_transient_error(e) or attempt == _MAX_RETRIES - 1:
                raise
            last_exc = e
            delay = _BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)
            time.sleep(delay)
    raise last_exc  # pragma: no cover - loop above always returns or raises

# Per-user tab isolation: each allowed user gets their own
# "Transactions_<id>" / "Budgets_<id>" tab so multiple people sharing
# the bot don't see or accidentally delete each other's entries. The
# first ID in ALLOWED_USER_ID is treated as the original owner and
# keeps using the plain "Transactions"/"Budgets" tab names, so data
# already in the sheet from before multi-user support stays right
# where it is — no manual migration needed.
ALLOWED_IDS_RAW = os.getenv("ALLOWED_USER_ID", "")
ALLOWED_IDS = [str(uid).strip() for uid in ALLOWED_IDS_RAW.split(",") if uid.strip()]


def _tab_name(base: str, user_id: int) -> str:
    if ALLOWED_IDS and str(user_id) == ALLOWED_IDS[0]:
        return base
    return f"{base}_{user_id}"


_service_cache = None

# Tabs we've already confirmed exist (or just created) in this process,
# so _ensure_*_sheet_exists doesn't re-fetch spreadsheet metadata on
# every single append — that extra round trip per call was one of the
# two reasons saving a large multi-line batch felt slow (see
# append_transactions_batch in transactions.py for the other: batching
# the rows themselves into one API call instead of one call per row).
_known_existing_tabs: set = set()


def _get_sheets_service():
    """
    Internal synchronous helper to initialize the Google Sheets API
    client. The built service object is cached at module level — the
    credentials file only needs to be read and the client only needs
    to be constructed once per process, not on every single API call.
    """
    global _service_cache
    if _service_cache is not None:
        return _service_cache

    if not os.path.exists(CREDENTIALS_PATH):
        raise FileNotFoundError(f"Google credentials file missing at: {CREDENTIALS_PATH}")

    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    _service_cache = build("sheets", "v4", credentials=creds)
    return _service_cache


def _tab_exists(sheet, tab_name: str) -> bool:
    metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    titles = [tab["properties"]["title"] for tab in metadata.get("sheets", [])]
    return tab_name in titles


def _get_sheet_id(sheet, tab_name: str):
    """Returns the numeric sheetId for a tab, or None if it doesn't exist."""
    metadata = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
    for tab in metadata.get("sheets", []):
        if tab["properties"]["title"] == tab_name:
            return tab["properties"]["sheetId"]
    return None
