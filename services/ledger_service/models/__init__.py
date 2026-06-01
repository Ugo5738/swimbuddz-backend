"""SQLAlchemy models for the Ledger Service.

Models are added in PR-1 (task P1.1): Organization, ChartOfAccounts,
CostCenter, JournalEntry, JournalLine, AccountBalance, Period, LedgerUser,
AuditLog. See docs/design/LEDGER_SERVICE_DESIGN.md §4.

⚠️ When adding a model, also import it in services/ledger_service/alembic/env.py
and add its table name to SERVICE_TABLES, or Alembic autogenerate won't detect
it (CLAUDE.md migration gotcha).
"""
