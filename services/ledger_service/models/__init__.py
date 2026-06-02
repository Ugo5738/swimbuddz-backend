"""SQLAlchemy models for the Ledger Service.

Importing this package registers every model on ``Base.metadata`` so Alembic
autogenerate can see them. ``alembic/env.py`` imports from here and lists the
owned tables in ``SERVICE_TABLES``.

PR-1 implements design doc §4.1 (core) + §4.5 (audit/users). Reconciliation,
FX, tax, and invoice tables (§4.2–4.4) arrive in their respective phases.
"""

from services.ledger_service.models.accounts import ChartOfAccounts, CostCenter
from services.ledger_service.models.audit import AuditLog, LedgerUser
from services.ledger_service.models.balances import AccountBalance
from services.ledger_service.models.enums import (
    LEDGER_ROLE_RANK,
    AccountingStandard,
    AccountType,
    AuditActionType,
    EntryStatus,
    LedgerRole,
    NormalBalance,
    OrgStatus,
    PeriodStatus,
    PeriodType,
    RecognitionMethod,
    RecognitionStatus,
    enum_values,
)
from services.ledger_service.models.journal import JournalEntry, JournalLine
from services.ledger_service.models.organization import Organization
from services.ledger_service.models.period import Period
from services.ledger_service.models.recognition import RevenueRecognitionSchedule
from services.ledger_service.models.reconciliation import (
    ExternalTransaction,
    ReconciliationBreak,
)

__all__ = [
    # Models
    "Organization",
    "ChartOfAccounts",
    "CostCenter",
    "JournalEntry",
    "JournalLine",
    "AccountBalance",
    "Period",
    "RevenueRecognitionSchedule",
    "ExternalTransaction",
    "ReconciliationBreak",
    "LedgerUser",
    "AuditLog",
    # Enums
    "AccountType",
    "NormalBalance",
    "AccountingStandard",
    "OrgStatus",
    "EntryStatus",
    "PeriodType",
    "PeriodStatus",
    "RecognitionMethod",
    "RecognitionStatus",
    "LedgerRole",
    "AuditActionType",
    "LEDGER_ROLE_RANK",
    "enum_values",
]
