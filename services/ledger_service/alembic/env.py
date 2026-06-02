"""Alembic configuration entrypoint for ledger service."""

# ruff: noqa: F401

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Ensure project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

from libs.common.config import get_settings
from libs.db.base import Base
from services.ledger_service.models import (  # noqa: F401
    AccountBalance,
    AuditLog,
    ChartOfAccounts,
    CostCenter,
    ExternalTransaction,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    JournalEntry,
    JournalLine,
    LedgerUser,
    Organization,
    Period,
    ReconciliationBreak,
    RevenueRecognitionSchedule,
)

settings = get_settings()
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Only migrate tables owned by this service. Populated in PR-1 (task P1.1) as
# models are added — keep in sync with the model imports above.
#
# NOTE ON TABLE NAMING (shared SwimBuddz DB): all services share one Postgres
# database, so generic names risk colliding with other services. Prefix the
# generic ones with `ledger_` (e.g. `ledger_organizations`, `ledger_periods`,
# `ledger_audit_log`, `ledger_users`); accounting-specific names
# (`journal_entries`, `chart_of_accounts`, …) are safe unprefixed. This keeps
# the B2B-extracted DB clean too. Final names decided in P1.1. Example:
SERVICE_TABLES: set[str] = {
    "ledger_organizations",
    "chart_of_accounts",
    "cost_centers",
    "journal_entries",
    "journal_lines",
    "account_balances",
    "ledger_periods",
    "revenue_recognition_schedules",
    "external_transactions",
    "reconciliation_breaks",
    "invoices",
    "invoice_lines",
    "invoice_sequences",
    "ledger_users",
    "ledger_audit_log",
}

url = settings.DATABASE_URL.replace("%", "%%")
config.set_main_option("sqlalchemy.url", url)


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in SERVICE_TABLES
    if type_ in ("index", "column", "foreign_key_constraint"):
        return obj.table.name in SERVICE_TABLES
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_ledger",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_ledger",
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # Disable psycopg auto-prepared statements to avoid duplicate name errors
        connect_args={"prepare_threshold": 0},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
