"""
Shared test fixtures available to all test layers.

Provides:
- Auth user mock factories (member, admin, coach, service_role)
- Auth override context managers
- Service role headers for contract tests
- Service client mock builders
"""

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from libs.auth.dependencies import (
    get_current_user,
    get_optional_user,
    require_admin,
    require_coach,
    require_service_role,
)
from libs.auth.models import AuthUser

# ---------------------------------------------------------------------------
# Auth User Factories
# ---------------------------------------------------------------------------


def make_member_user(
    user_id: str = "test-member-id",
    email: str = "member@test.com",
    roles: list[str] | None = None,
) -> AuthUser:
    """Create an AuthUser representing a regular member."""
    return AuthUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        app_metadata={"roles": roles or ["member"]},
        user_metadata={},
    )


def make_admin_user(
    user_id: str = "test-admin-id",
    email: str = "admin@admin.com",
) -> AuthUser:
    """Create an AuthUser representing an admin."""
    return AuthUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        app_metadata={"roles": ["admin", "member"]},
        user_metadata={},
    )


def make_coach_user(
    user_id: str = "test-coach-id",
    email: str = "coach@test.com",
) -> AuthUser:
    """Create an AuthUser representing a coach."""
    return AuthUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        app_metadata={"roles": ["coach", "member"]},
        user_metadata={},
    )


def make_service_role_user(
    service_name: str = "test-service",
) -> AuthUser:
    """Create an AuthUser representing an internal service call."""
    return AuthUser(
        user_id=f"service:{service_name}",
        email=None,
        role="service_role",
        app_metadata={},
        user_metadata={},
    )


# ---------------------------------------------------------------------------
# Auth Override Helpers
# ---------------------------------------------------------------------------


@contextmanager
def override_auth(app, user: AuthUser):
    """
    Context manager that overrides auth dependencies on a FastAPI app.

    Usage:
        with override_auth(members_app, make_admin_user()):
            response = await client.get("/admin/members")
    """

    async def _get_user():
        return user

    app.dependency_overrides[get_current_user] = _get_user
    app.dependency_overrides[require_admin] = _get_user
    app.dependency_overrides[require_coach] = _get_user
    app.dependency_overrides[require_service_role] = _get_user

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(require_admin, None)
        app.dependency_overrides.pop(require_coach, None)
        app.dependency_overrides.pop(require_service_role, None)


@contextmanager
def override_auth_as_member(app, user: AuthUser | None = None):
    """Override auth as a regular member (NO admin/coach access)."""
    user = user or make_member_user()

    async def _get_user():
        return user

    app.dependency_overrides[get_current_user] = _get_user
    # Intentionally do NOT override require_admin or require_coach
    # so those endpoints will properly 403

    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Service Role Headers (for contract tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def service_role_headers() -> dict:
    """
    Headers that simulate a service-role JWT for internal endpoint tests.

    In contract tests, we override the require_service_role dependency
    instead of passing a real JWT, so this is just a placeholder token.
    """
    return {"Authorization": "Bearer service-role-mock-token"}


# ---------------------------------------------------------------------------
# Service Client Mock Builders
# ---------------------------------------------------------------------------


def build_member_mock(
    member_id: str = "00000000-0000-0000-0000-000000000001",
    auth_id: str = "auth-test-123",
    email: str = "member@test.com",
    full_name: str = "Test Member",
    first_name: str = "Test",
    last_name: str = "Member",
    is_active: bool = True,
    **extra: Any,
) -> dict:
    """
    Build a dict matching GET /internal/members/{id} response shape.
    Use this to mock service client responses in integration tests.
    """
    data = {
        "id": member_id,
        "auth_id": auth_id,
        "email": email,
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        "is_active": is_active,
    }
    data.update(extra)
    return data


def build_session_mock(
    session_id: str = "00000000-0000-0000-0000-000000000002",
    title: str = "Test Session",
    starts_at: str = "2026-03-01T10:00:00+01:00",
    ends_at: str = "2026-03-01T12:00:00+01:00",
    status: str = "SCHEDULED",
    session_type: str = "CLUB",
    location: str = "SUNFIT_POOL",
    **extra: Any,
) -> dict:
    """Build a dict matching GET /internal/sessions/{id}."""
    data = {
        "id": session_id,
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "status": status,
        "session_type": session_type,
        "location": location,
    }
    data.update(extra)
    return data


def build_coach_profile_mock(
    member_id: str = "00000000-0000-0000-0000-000000000001",
    display_name: str = "Coach Test",
    learn_to_swim_grade: str = "grade_2",
    total_coaching_hours: int = 100,
    status: str = "approved",
    **extra: Any,
) -> dict:
    """Build a dict matching GET /internal/coaches/{id}/profile."""
    data = {
        "member_id": member_id,
        "display_name": display_name,
        "learn_to_swim_grade": learn_to_swim_grade,
        "total_coaching_hours": total_coaching_hours,
        "status": status,
    }
    data.update(extra)
    return data


def mock_service_client(**overrides):
    """
    Patch libs.common.service_client functions with sensible defaults.

    Usage:
        with mock_service_client(
            get_member_by_id={"id": "...", "full_name": "Alice"},
        ) as mocks:
            response = await client.post("/academy/enrollments", ...)

    Any function not explicitly overridden returns None (simulating "not found").
    """
    defaults = {
        "get_member_by_auth_id": None,
        "get_member_by_id": None,
        "get_members_bulk": [],
        "get_coach_profile": None,
        "get_session_by_id": None,
        "get_next_session_for_cohort": None,
        "get_session_ids_for_cohort": [],
        "get_coach_readiness_data": None,
        "get_eligible_coaches": [],
    }
    defaults.update(overrides)

    class MockContainer:
        pass

    container = MockContainer()

    patches = {}
    for fn_name, return_value in defaults.items():
        mock = AsyncMock(return_value=return_value)
        patches[fn_name] = patch(
            f"libs.common.service_client.{fn_name}",
            mock,
        )
        setattr(container, fn_name, mock)

    @contextmanager
    def _ctx():
        entered = []
        try:
            for p in patches.values():
                p.start()
                entered.append(p)
            yield container
        finally:
            for p in entered:
                p.stop()

    return _ctx()


# ---------------------------------------------------------------------------
# Per-Service AsyncClient Fixtures
# ---------------------------------------------------------------------------
# These live here (not in integration/conftest.py) so they're accessible
# to BOTH integration and contract tests.

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from libs.db.session import get_async_db


def _db_override(db_session):
    """Create a dependency override that returns the test db_session."""

    async def _get_db():
        yield db_session

    return _get_db


def _admin_override():
    """Create auth override that returns an admin user."""
    admin = make_admin_user()

    async def _get_admin():
        return admin

    return _get_admin


def _service_role_override():
    """Create auth override that returns a service role user."""
    svc = make_service_role_user()

    async def _get_svc():
        return svc

    return _get_svc


def _wire_app(app, db_session):
    """Apply standard dependency overrides to a FastAPI app."""
    app.dependency_overrides[get_async_db] = _db_override(db_session)
    app.dependency_overrides[get_current_user] = _admin_override()
    app.dependency_overrides[get_optional_user] = _admin_override()
    app.dependency_overrides[require_admin] = _admin_override()
    app.dependency_overrides[require_coach] = _admin_override()
    app.dependency_overrides[require_service_role] = _service_role_override()


@pytest_asyncio.fixture
async def members_client(db_session):
    """AsyncClient for the members service with admin auth."""
    from services.members_service.app.main import app as members_app

    _wire_app(members_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=members_app),
        base_url="http://test",
    ) as client:
        yield client
    members_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def sessions_client(db_session):
    """AsyncClient for the sessions service with admin auth."""
    from services.sessions_service.app.main import app as sessions_app

    _wire_app(sessions_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=sessions_app),
        base_url="http://test",
    ) as client:
        yield client
    sessions_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def attendance_client(db_session):
    """AsyncClient for the attendance service with admin auth."""
    from services.attendance_service.app.main import app as attendance_app

    _wire_app(attendance_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=attendance_app),
        base_url="http://test",
    ) as client:
        yield client
    attendance_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def academy_client(db_session):
    """AsyncClient for the academy service with admin auth."""
    from services.academy_service.app.main import app as academy_app

    _wire_app(academy_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=academy_app),
        base_url="http://test",
    ) as client:
        yield client
    academy_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def communications_client(db_session):
    """AsyncClient for the communications service with admin auth."""
    from services.communications_service.app.main import app as comms_app

    _wire_app(comms_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=comms_app),
        base_url="http://test",
    ) as client:
        yield client
    comms_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def payments_client(db_session):
    """AsyncClient for the payments service with admin auth."""
    from services.payments_service.app.main import app as payments_app

    _wire_app(payments_app, db_session)
    async with AsyncClient(
        transport=ASGITransport(app=payments_app),
        base_url="http://test",
    ) as client:
        yield client
    payments_app.dependency_overrides.clear()
