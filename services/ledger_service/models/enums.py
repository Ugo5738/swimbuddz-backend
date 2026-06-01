"""Enums for the Ledger Service models.

Persisted as their lowercase string ``value`` via ``values_callable=enum_values``
(same pattern as wallet_service). Never change a stored value — it would break
existing rows.
"""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class AccountType(str, enum.Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"
    CONTRA_ASSET = "contra_asset"
    CONTRA_LIABILITY = "contra_liability"
    CONTRA_REVENUE = "contra_revenue"
    CONTRA_EXPENSE = "contra_expense"


class NormalBalance(str, enum.Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class AccountingStandard(str, enum.Enum):
    ACCRUAL = "accrual"
    CASH = "cash"


class OrgStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class EntryStatus(str, enum.Enum):
    POSTED = "posted"
    REVERSED = "reversed"


class PeriodType(str, enum.Enum):
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class PeriodStatus(str, enum.Enum):
    OPEN = "open"
    SOFT_CLOSED = "soft_closed"
    HARD_CLOSED = "hard_closed"


class LedgerRole(str, enum.Enum):
    """Finance-team roles (design doc §15). Hierarchy: owner > admin > accountant > viewer."""

    VIEWER = "viewer"
    ACCOUNTANT = "accountant"
    ADMIN = "admin"
    OWNER = "owner"


class AuditActionType(str, enum.Enum):
    ENTRY_POSTED = "entry_posted"
    ENTRY_REVERSED = "entry_reversed"
    PERIOD_CLOSED = "period_closed"
    INVOICE_ISSUED = "invoice_issued"
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_MODIFIED = "account_modified"
    TAX_CODE_MODIFIED = "tax_code_modified"
    RECONCILIATION_MATCHED = "reconciliation_matched"
    USER_ADDED = "user_added"
    USER_ROLE_CHANGED = "user_role_changed"
    USER_DEACTIVATED = "user_deactivated"


# Ordered rank for LedgerRole capability inheritance (owner ⊇ admin ⊇ accountant ⊇ viewer).
LEDGER_ROLE_RANK = {
    LedgerRole.VIEWER: 0,
    LedgerRole.ACCOUNTANT: 1,
    LedgerRole.ADMIN: 2,
    LedgerRole.OWNER: 3,
}
