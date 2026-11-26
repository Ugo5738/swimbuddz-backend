"""Dedicated database session factory for the detachable Ledger Service.

When ``LEDGER_DATABASE_URL`` is configured (for example pointing to a
``NOBYPASSRLS`` PostgreSQL role like ``ledger_app`` or an entirely standalone
PostgreSQL database for multi-company SaaS deployment), ``ledger_service`` uses
its own isolated engine and session pool.

When ``LEDGER_DATABASE_URL`` is unset (SwimBuddz single-DB local/dev/Phase 1),
it cleanly reuses ``libs.db.config.AsyncSessionLocal`` so existing tests and
deployments keep working with zero configuration changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator

from libs.common.config import get_settings
from libs.db.config import AsyncSessionLocal, should_use_null_pool
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _build_ledger_sessionmaker(url: str) -> async_sessionmaker[AsyncSession]:
    settings = get_settings()
    parsed = make_url(url)
    host = (parsed.host or "").lower()
    using_supabase_pooler = host.endswith(".pooler.supabase.com")
    use_null_pool = should_use_null_pool(
        host, local_pool_enabled=settings.DB_USE_LOCAL_POOL
    )

    connect_args: dict = {}
    if settings.DB_APPLICATION_NAME:
        connect_args["application_name"] = settings.DB_APPLICATION_NAME
    if host.endswith(".supabase.com") or using_supabase_pooler:
        connect_args["sslmode"] = "require"
        connect_args["keepalives"] = 1
        connect_args["keepalives_idle"] = 30
        connect_args["keepalives_interval"] = 10
        connect_args["keepalives_count"] = 5
    if using_supabase_pooler:
        connect_args["prepare_threshold"] = None

    engine_kwargs: dict = {
        "echo": settings.ENVIRONMENT == "local",
        "future": True,
        "pool_pre_ping": True,
        "connect_args": connect_args,
    }
    if use_null_pool:
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs.update(
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=settings.DB_POOL_TIMEOUT,
            pool_recycle=settings.DB_POOL_RECYCLE,
        )

    engine = create_async_engine(url, **engine_kwargs)
    return async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


@lru_cache(maxsize=2)
def _cached_sessionmaker(url: str) -> async_sessionmaker[AsyncSession]:
    return _build_ledger_sessionmaker(url)


def get_ledger_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the Ledger sessionmaker (dedicated URL if set, else shared DB)."""
    dedicated_url = (get_settings().LEDGER_DATABASE_URL or "").strip()
    if dedicated_url:
        return _cached_sessionmaker(dedicated_url)
    return AsyncSessionLocal


async def get_ledger_raw_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session on Ledger's configured DB without org RLS yet."""
    factory = get_ledger_sessionmaker()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
