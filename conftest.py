from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.exc import OperationalError

from libs.common.config import get_settings
from libs.db.base import Base
from services.gateway_service.app.main import app

settings = get_settings()


@pytest_asyncio.fixture
async def test_engine():
    """
    Create a test engine that connects to the database.
    In a real scenario, we might want to create a separate test DB.
    For now, we'll use the main DB but wrap tests in transactions.
    """
    # Fix for running tests on host where host.docker.internal might not resolve
    db_url = settings.DATABASE_URL.replace("host.docker.internal", "localhost")

    engine = create_async_engine(db_url, future=True)

    # Create tables
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except OperationalError:
        await engine.dispose()
        pytest.skip("Database not available for tests")

    yield engine

    # Drop tables after session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session that rolls back after the test.
    We use join_transaction_mode="create_savepoint" to allow the session to be used
    as if it were a top-level session (supporting commit/rollback) while actually
    running inside a transaction that we rollback at the end.
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


@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    """
    Yield an AsyncClient with the app and overridden DB dependency.
    """
    from libs.db.session import get_async_db

    app.dependency_overrides[get_async_db] = lambda: db_session

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict:
    """
    Return headers for a mocked authenticated user.
    Since we mock the `get_current_user` dependency in specific tests or globally,
    this might just be a placeholder or used if we mock the JWT decoding.
    For now, we'll rely on dependency overrides for auth in tests,
    but this can be useful if we want to pass a real-looking token.
    """
    return {"Authorization": "Bearer mock-token"}
