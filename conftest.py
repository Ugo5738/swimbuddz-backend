"""
Root test configuration — database fixtures only.

This file handles:
1. Engine creation against the dev database (or test database if configured)
2. Transactional session with rollback after each test
3. Importing all models so Base.metadata knows every table

It does NOT handle:
- HTTP clients (see tests/integration/conftest.py)
- Auth mocking (see tests/conftest.py)
- Service wiring (see tests/integration/conftest.py)
"""

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio

# Load .env.dev for tests
from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

env_dev_path = os.path.join(os.path.dirname(__file__), ".env.dev")
if os.path.exists(env_dev_path):
    load_dotenv(env_dev_path, override=True)

from libs.common.config import get_settings

# ---------------------------------------------------------------------------
# DO NOT import service models here.
#
# Multiple services define MemberRef stubs (extend_existing=True) on the
# "members" table. If members_service.models (with full Member + relationships)
# is imported alongside any other service's models, SQLAlchemy's mapper
# breaks (UnmappedColumnError on Member.profile).
#
# Models are loaded automatically when each service's FastAPI app is
# imported by the per-service client fixtures (members_client, etc.).
# ---------------------------------------------------------------------------

# Clear cached settings to reload with new env vars
get_settings.cache_clear()
settings = get_settings()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """
    Create a test engine connected to the database.

    Uses the dev database with transactional isolation (no data persists).
    Tables are managed by Alembic migrations (run reset.sh dev first).
    We do NOT create_all/drop_all — that conflicts with existing migrations.
    If the database is unreachable, the test is skipped.
    """
    # Prefer DATABASE_SESSION_URL (direct connection, port 5432) over
    # DATABASE_URL (pooler, port 6543). The pooler doesn't support the
    # transactional isolation our tests need. This mirrors what reset.sh does.
    raw_url = os.environ.get("DATABASE_SESSION_URL") or settings.DATABASE_URL
    db_url = raw_url.replace("host.docker.internal", "localhost")

    db_parsed = make_url(db_url)
    db_host = (db_parsed.host or "").lower()
    use_null_pool = db_host.endswith(".pooler.supabase.com")

    connect_args: dict = {}
    if db_host.endswith(".supabase.com") or use_null_pool:
        # Supabase-managed Postgres often drops idle TLS connections; keepalives
        # + pre-ping reduce intermittent SSL EOF failures in long test runs.
        connect_args.update(
            {
                "sslmode": "require",
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            }
        )

    engine_kwargs: dict = {
        "future": True,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if use_null_pool:
        # If URL points at Supabase pooler, avoid pooling a pool.
        engine_kwargs["poolclass"] = NullPool
    else:
        # Recycle long-lived pooled connections to avoid stale TLS sockets.
        engine_kwargs["pool_recycle"] = 300

    engine = create_async_engine(db_url, **engine_kwargs)

    # Verify database is reachable (tables already exist from migrations)
    try:
        async with engine.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text("SELECT 1"))
    except OperationalError:
        await engine.dispose()
        pytest.skip("Database not available for tests")

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session wrapped in a transaction that rolls back after
    the test. This means tests never persist data.

    Uses join_transaction_mode="create_savepoint" so the session can call
    commit() and rollback() as if it were a top-level session, while actually
    running inside our outer transaction.
    """
    connection = await test_engine.connect()
    transaction = await connection.begin()

    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = session_factory()

    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
