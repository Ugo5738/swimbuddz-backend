"""Alembic configuration entrypoint for wallet service."""

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
from services.wallet_service.models import (  # noqa: F401
    # Phase 1 — Active
    Wallet,
    WalletTransaction,
    WalletTopup,
    PromotionalBubbleGrant,
    WalletAuditLog,
    # Phase 3 — Referral & Rewards stubs
    ReferralRecord,
    ReferralCode,
    RewardRule,
    WalletEvent,
    MemberRewardHistory,
    # Phase 4 — Family wallet stub
    FamilyWalletLink,
    # Phase 5 — Corporate wallet stubs
    CorporateWallet,
    CorporateWalletMember,
)

settings = get_settings()
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Only migrate tables owned by this service
SERVICE_TABLES = {
    # Phase 1
    "wallets",
    "wallet_transactions",
    "wallet_topups",
    "promotional_bubble_grants",
    "wallet_audit_logs",
    # Phase 3
    "referral_records",
    "referral_codes",
    "reward_rules",
    "wallet_events",
    "member_reward_history",
    # Phase 4
    "family_wallet_links",
    # Phase 5
    "corporate_wallets",
    "corporate_wallet_members",
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
        version_table="alembic_version_wallet",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_wallet",
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
