"""Alembic configuration entrypoint for transport service."""

# ruff: noqa: F401

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Ensure project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(PROJECT_ROOT))

from libs.common.config import get_settings
from libs.db.base import Base
from services.transport_service.models import (
    RideArea,
    PickupLocation,
    RouteInfo,
    RidePreference,
)  # noqa: F401
from sqlalchemy import Table, Column
from sqlalchemy.dialects.postgresql import UUID

settings = get_settings()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

url = settings.DATABASE_URL.replace("%", "%%")
config.set_main_option("sqlalchemy.url", url)

# Placeholder referenced tables for FK ordering
Table(
    "sessions",
    target_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    extend_existing=True,
)
Table(
    "members",
    target_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    extend_existing=True,
)
STUB_TABLES = {"sessions", "members"}


def include_object(obj, name, type_, reflected, compare_to):
    if name in STUB_TABLES:
        return False
    if reflected and name not in target_metadata.tables:
        return False
    if type_ == "table":
        return name in target_metadata.tables
    if type_ in ("index", "column", "foreign_key_constraint"):
        return obj.table.name in target_metadata.tables
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_transport",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table="alembic_version_transport",
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
