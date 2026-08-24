"""
core.storage — Google Sheets–backed persistence, split by concern:

- _client.py       shared API client, retry logic, tab-name resolution
- transactions.py  transaction row CRUD
- budgets.py       budget-limit row CRUD

This __init__ re-exports the public functions from both so callers
can do `from core.storage import append_transaction, get_budgets`
without needing to know which submodule each lives in — the same flat
import surface the old single-file core/sheets.py offered.
"""
from core.storage.transactions import (
    append_transaction,
    append_transactions_batch,
    get_last_transaction,
    delete_last_transaction,
    get_last_n_transactions,
    delete_last_n_transactions,
    get_all_transactions,
    get_all_transactions_with_index,
    get_recent_transactions_with_index,
    get_transaction_row,
    update_transaction_row,
    delete_transaction_row,
)
from core.storage.budgets import (
    get_budgets,
    set_budget,
    delete_budget,
)
from core.storage._client import ALLOWED_IDS

__all__ = [
    "append_transaction",
    "append_transactions_batch",
    "get_last_transaction",
    "delete_last_transaction",
    "get_last_n_transactions",
    "delete_last_n_transactions",
    "get_all_transactions",
    "get_all_transactions_with_index",
    "get_recent_transactions_with_index",
    "get_transaction_row",
    "update_transaction_row",
    "delete_transaction_row",
    "get_budgets",
    "set_budget",
    "delete_budget",
    "ALLOWED_IDS",
]
