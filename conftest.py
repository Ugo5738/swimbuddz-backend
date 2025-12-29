import inspect
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.exc import OperationalError

# Load .env.dev for tests if DATABASE_URL points to local Docker
# This allows tests to run against the remote Supabase database
from dotenv import load_dotenv

# Try loading .env.dev for development environment settings
env_dev_path = os.path.join(os.path.dirname(__file__), ".env.dev")
if os.path.exists(env_dev_path):
    load_dotenv(env_dev_path, override=True)

from libs.common.config import get_settings, Settings
from libs.db.base import Base
from services.gateway_service.app.main import app

# Import all models so metadata includes every table (e.g., session_templates)
from services.members_service import models as _member_models  # noqa: F401
from services.sessions_service import models as _session_models  # noqa: F401
from services.sessions_service import session_template as _session_template  # noqa: F401

# Clear cached settings to reload with new env vars
get_settings.cache_clear()
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
    # Also override DB dependency for in-process service apps used in tests
    from services.members_service.app.main import app as members_app
    from services.sessions_service.app.main import app as sessions_app

    members_app.dependency_overrides[get_async_db] = lambda: db_session
    sessions_app.dependency_overrides[get_async_db] = lambda: db_session

    # Share mocked auth dependency from gateway app with service apps
    from libs.auth.dependencies import get_current_user
    from libs.auth.models import AuthUser

    async def _proxy_current_user():
        override = app.dependency_overrides.get(get_current_user)
        if override:
            result = override()
            if inspect.isawaitable(result):
                return await result
            return result
        # Fallback dummy user for tests that don't override auth explicitly
        return AuthUser(user_id="test-user", email="test@example.com", role="member")

    members_app.dependency_overrides[get_current_user] = _proxy_current_user

    # Route gateway service clients to in-process FastAPI apps instead of external URLs
    from services.gateway_service.app import clients

    class InAppServiceClient(clients.ServiceClient):
        def __init__(self, target_app):
            super().__init__(base_url="http://test")
            self.target_app = target_app

        async def _request(self, method: str, path: str, **kwargs):
            async with AsyncClient(
                transport=ASGITransport(app=self.target_app), base_url="http://test"
            ) as internal_client:
                response = await internal_client.request(method, path, **kwargs)
                response.raise_for_status()
                return response

    original_members_client = clients.members_client
    original_sessions_client = clients.sessions_client
    clients.members_client = InAppServiceClient(members_app)
    clients.sessions_client = InAppServiceClient(sessions_app)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    members_app.dependency_overrides.clear()
    sessions_app.dependency_overrides.clear()
    clients.members_client = original_members_client
    clients.sessions_client = original_sessions_client


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
