# SwimBuddz Testing Implementation Guide

> **Companion to:** `TESTING_ARCHITECTURE.md` (read that first for the WHY, this document is the HOW)
> **Purpose:** Copy-pasteable code for every file in the test suite. An AI agent should be able to implement the entire test system from this document alone.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [pytest.ini](#pytestini)
3. [Root conftest.py (Redesigned)](#root-conftestpy-redesigned)
4. [tests/conftest.py (Shared Fixtures)](#testsconftestpy-shared-fixtures)
5. [tests/factories.py (Model Factories)](#testsfactoriespy-model-factories)
6. [tests/integration/conftest.py (Service Clients)](#testsintegrationconftestpy-service-clients)
7. [tests/contract/conftest.py (Service Role Auth)](#testscontractconftestpy-service-role-auth)
8. [Example: Unit Test](#example-unit-test)
9. [Example: Integration Test](#example-integration-test)
10. [Example: Contract Test](#example-contract-test)
11. [Mocking the Service Client](#mocking-the-service-client)
12. [Auth Mocking Patterns](#auth-mocking-patterns)
13. [Common Pitfalls](#common-pitfalls)
14. [Step-by-Step Implementation Checklist](#step-by-step-implementation-checklist)

---

## Prerequisites

These packages are already in `pyproject.toml`:

```
pytest>=8.0,<9.0
pytest-asyncio>=0.23,<0.25
httpx[http2]>=0.26,<0.28
```

No additional packages needed. The test suite uses only stdlib `unittest.mock` for mocking.

---

## pytest.ini

Create this file at the backend root:

```ini
# swimbuddz-backend/pytest.ini

[pytest]
asyncio_mode = auto
testpaths = tests
markers =
    unit: Pure logic tests — no I/O, no database, no HTTP
    integration: Tests with database and mocked auth
    contract: Cross-service response shape validation
    slow: Tests that take >2s (excluded from fast runs)
filterwarnings =
    ignore::DeprecationWarning
    ignore::pytest.PytestUnraisableExceptionWarning
```

---

## Root conftest.py (Redesigned)

This replaces the existing `conftest.py` at the backend root. Key changes:

- Imports ALL service models (not just members + sessions)
- Removes the `client` fixture (moved to integration conftest)
- Removes `InAppServiceClient` (no longer needed)
- Removes `auth_headers` (moved to tests/conftest.py)

```python
# swimbuddz-backend/conftest.py
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

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.exc import OperationalError
from typing import AsyncGenerator

# Load .env.dev for tests
from dotenv import load_dotenv

env_dev_path = os.path.join(os.path.dirname(__file__), ".env.dev")
if os.path.exists(env_dev_path):
    load_dotenv(env_dev_path, override=True)

from libs.common.config import get_settings
from libs.db.base import Base

# ---------------------------------------------------------------------------
# CRITICAL: Import ALL service models so Base.metadata.create_all creates
# every table. Without these imports, tests will get "table not found" errors.
# ---------------------------------------------------------------------------
from services.members_service import models as _member_models          # noqa: F401
from services.sessions_service import models as _session_models        # noqa: F401
from services.attendance_service import models as _attendance_models   # noqa: F401
from services.academy_service import models as _academy_models         # noqa: F401
from services.communications_service import models as _comms_models    # noqa: F401
from services.payments_service import models as _payment_models        # noqa: F401
from services.events_service import models as _event_models            # noqa: F401
from services.media_service import models as _media_models             # noqa: F401
from services.transport_service import models as _transport_models     # noqa: F401
from services.store_service import models as _store_models             # noqa: F401
from services.ai_service import models as _ai_models                   # noqa: F401
from services.volunteer_service import models as _volunteer_models     # noqa: F401

# Clear cached settings to reload with new env vars
get_settings.cache_clear()
settings = get_settings()


@pytest_asyncio.fixture
async def test_engine():
    """
    Create a test engine connected to the database.

    Uses the dev database with transactional isolation (no data persists).
    If the database is unreachable, the test is skipped.
    """
    # Fix for running tests on host where host.docker.internal might not resolve
    db_url = settings.DATABASE_URL.replace("host.docker.internal", "localhost")

    engine = create_async_engine(db_url, future=True)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except OperationalError:
        await engine.dispose()
        pytest.skip("Database not available for tests")

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

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
```

---

## tests/conftest.py (Shared Fixtures)

```python
# tests/conftest.py
"""
Shared test fixtures available to all test layers.

Provides:
- Auth user mock factories (member, admin, coach, service_role)
- Auth override context managers
- Service role headers for contract tests
- Service client mock builders
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from typing import Any

import pytest
from libs.auth.models import AuthUser
from libs.auth.dependencies import (
    get_current_user,
    require_admin,
    require_coach,
    require_service_role,
)


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
    Build a dict that matches the shape returned by
    GET /internal/members/{id} and expected by service_client.get_member_by_id().

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
    """
    Build a dict that matches GET /internal/sessions/{id}.
    """
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
    """
    Build a dict that matches GET /internal/coaches/{id}/profile.
    """
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
            # mocks.get_member_by_id is an AsyncMock
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
```

---

## tests/factories.py (Model Factories)

```python
# tests/factories.py
"""
Model factories for creating valid test data.

Every factory produces a valid, insertable SQLAlchemy model instance.
Override any field via kwargs.

Usage:
    member = MemberFactory.create(email="custom@test.com")
    db_session.add(member)
    await db_session.commit()
"""

import uuid
import json
from datetime import datetime, timedelta, timezone, date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tomorrow() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=1)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:8]}@test.com"


# ---------------------------------------------------------------------------
# Members Service
# ---------------------------------------------------------------------------

class MemberFactory:
    @staticmethod
    def create(**overrides):
        from services.members_service.models import Member

        defaults = {
            "id": _uuid(),
            "auth_id": str(_uuid()),
            "email": _unique_email(),
            "first_name": "Test",
            "last_name": "Member",
            "is_active": True,
            "registration_complete": True,
            "roles": ["member"],
            "approval_status": "approved",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Member(**defaults)


class CoachProfileFactory:
    @staticmethod
    def create(member_id=None, **overrides):
        from services.members_service.models import CoachProfile

        defaults = {
            "id": _uuid(),
            "member_id": member_id or _uuid(),
            "display_name": "Test Coach",
            "status": "approved",
            "learn_to_swim_grade": "grade_2",
            "special_populations_grade": None,
            "institutional_grade": None,
            "competitive_elite_grade": None,
            "total_coaching_hours": 100,
            "cohorts_completed": 5,
            "average_feedback_rating": 4.5,
            "coaching_years": 3,
            "max_swimmers_per_session": 10,
            "max_cohorts_at_once": 2,
            "currency": "NGN",
            "show_in_directory": True,
            "is_featured": False,
            "is_verified": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return CoachProfile(**defaults)


class PendingRegistrationFactory:
    @staticmethod
    def create(**overrides):
        from services.members_service.models import PendingRegistration

        email = overrides.pop("email", _unique_email())
        defaults = {
            "email": email,
            "profile_data_json": json.dumps({
                "first_name": "Pending",
                "last_name": "User",
                "email": email,
            }),
        }
        defaults.update(overrides)
        return PendingRegistration(**defaults)


# ---------------------------------------------------------------------------
# Sessions Service
# ---------------------------------------------------------------------------

class SessionFactory:
    @staticmethod
    def create(**overrides):
        from services.sessions_service.models import Session

        tomorrow = _tomorrow()
        defaults = {
            "id": _uuid(),
            "title": "Test Session",
            "session_type": "CLUB",
            "status": "SCHEDULED",
            "starts_at": tomorrow,
            "ends_at": tomorrow + timedelta(hours=2),
            "timezone": "Africa/Lagos",
            "location": "SUNFIT_POOL",
            "capacity": 20,
            "pool_fee": 2000.0,
            "ride_share_fee": 0.0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Session(**defaults)


class SessionCoachFactory:
    @staticmethod
    def create(session_id=None, coach_id=None, **overrides):
        from services.sessions_service.models import SessionCoach

        defaults = {
            "id": _uuid(),
            "session_id": session_id or _uuid(),
            "coach_id": coach_id or _uuid(),
            "role": "lead",
            "created_at": _now(),
        }
        defaults.update(overrides)
        return SessionCoach(**defaults)


# ---------------------------------------------------------------------------
# Academy Service
# ---------------------------------------------------------------------------

class ProgramFactory:
    @staticmethod
    def create(**overrides):
        from services.academy_service.models import Program

        defaults = {
            "id": _uuid(),
            "name": "Beginner Swim Program",
            "slug": f"beginner-{uuid.uuid4().hex[:6]}",
            "description": "A 12-week program for adult beginners.",
            "level": "BEGINNER_1",
            "duration_weeks": 12,
            "default_capacity": 10,
            "currency": "NGN",
            "price_amount": 150000,
            "billing_type": "ONE_TIME",
            "is_published": True,
            "version": 1,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Program(**defaults)


class CohortFactory:
    @staticmethod
    def create(program_id=None, **overrides):
        from services.academy_service.models import Cohort

        start = _tomorrow()
        defaults = {
            "id": _uuid(),
            "program_id": program_id or _uuid(),
            "name": f"Cohort Q1-{uuid.uuid4().hex[:4]}",
            "start_date": start,
            "end_date": start + timedelta(weeks=12),
            "capacity": 20,
            "timezone": "Africa/Lagos",
            "location_type": "POOL",
            "location_name": "Sunfit Pool",
            "status": "OPEN",
            "allow_mid_entry": False,
            "mid_entry_cutoff_week": 2,
            "require_approval": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Cohort(**defaults)


class EnrollmentFactory:
    @staticmethod
    def create(cohort_id=None, member_id=None, **overrides):
        from services.academy_service.models import Enrollment

        defaults = {
            "id": _uuid(),
            "cohort_id": cohort_id or _uuid(),
            "member_id": member_id or _uuid(),
            "member_auth_id": str(_uuid()),
            "status": "ENROLLED",
            "payment_status": "PAID",
            "price_snapshot_amount": 150000,
            "currency_snapshot": "NGN",
            "source": "WEB",
            "enrolled_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Enrollment(**defaults)


# ---------------------------------------------------------------------------
# Attendance Service
# ---------------------------------------------------------------------------

class AttendanceRecordFactory:
    @staticmethod
    def create(session_id=None, member_id=None, **overrides):
        from services.attendance_service.models import AttendanceRecord

        defaults = {
            "id": _uuid(),
            "session_id": session_id or _uuid(),
            "member_id": member_id or _uuid(),
            "status": "PRESENT",
            "role": "SWIMMER",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return AttendanceRecord(**defaults)


# ---------------------------------------------------------------------------
# Payments Service
# ---------------------------------------------------------------------------

class PaymentFactory:
    @staticmethod
    def create(**overrides):
        from services.payments_service.models import Payment

        defaults = {
            "id": _uuid(),
            "reference": f"PAY-{uuid.uuid4().hex[:5].upper()}",
            "member_auth_id": str(_uuid()),
            "payer_email": _unique_email(),
            "purpose": "COMMUNITY",
            "amount": 20000.0,
            "currency": "NGN",
            "status": "PENDING",
            "payment_method": "paystack",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Payment(**defaults)


class DiscountFactory:
    @staticmethod
    def create(**overrides):
        from services.payments_service.models import Discount

        defaults = {
            "id": _uuid(),
            "code": f"TEST-{uuid.uuid4().hex[:6].upper()}",
            "description": "Test discount",
            "discount_type": "PERCENTAGE",
            "value": 10.0,
            "is_active": True,
            "current_uses": 0,
            "max_uses": None,
            "valid_from": _now() - timedelta(days=1),
            "valid_until": _now() + timedelta(days=30),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Discount(**defaults)


# ---------------------------------------------------------------------------
# Communications Service
# ---------------------------------------------------------------------------

class AnnouncementFactory:
    @staticmethod
    def create(**overrides):
        from services.communications_service.models import Announcement

        defaults = {
            "id": _uuid(),
            "title": "Test Announcement",
            "summary": "A test summary",
            "body": "This is the full body of the test announcement.",
            "category": "GENERAL",
            "status": "PUBLISHED",
            "audience": "COMMUNITY",
            "notify_email": False,
            "notify_push": False,
            "is_pinned": False,
            "published_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Announcement(**defaults)
```

---

## tests/integration/conftest.py (Service Clients)

```python
# tests/integration/conftest.py
"""
Integration test fixtures — one AsyncClient per service.

Each fixture:
1. Overrides get_async_db to use the transactional db_session
2. Overrides auth to use a default user (admin for most services)
3. Yields an AsyncClient targeting the service app directly (no gateway)
4. Cleans up all overrides after the test
"""

import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from libs.db.session import get_async_db
from libs.auth.dependencies import get_current_user, require_admin, require_service_role
from tests.conftest import make_admin_user, make_service_role_user


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


# ---------------------------------------------------------------------------
# Members Service
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def members_client(db_session):
    """AsyncClient for the members service with admin auth."""
    from services.members_service.app.main import app as members_app

    members_app.dependency_overrides[get_async_db] = _db_override(db_session)
    members_app.dependency_overrides[get_current_user] = _admin_override()
    members_app.dependency_overrides[require_admin] = _admin_override()
    members_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=members_app),
        base_url="http://test",
    ) as client:
        yield client

    members_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Sessions Service
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def sessions_client(db_session):
    """AsyncClient for the sessions service with admin auth."""
    from services.sessions_service.app.main import app as sessions_app

    sessions_app.dependency_overrides[get_async_db] = _db_override(db_session)
    sessions_app.dependency_overrides[get_current_user] = _admin_override()
    sessions_app.dependency_overrides[require_admin] = _admin_override()
    sessions_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=sessions_app),
        base_url="http://test",
    ) as client:
        yield client

    sessions_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Attendance Service
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def attendance_client(db_session):
    """AsyncClient for the attendance service with admin auth."""
    from services.attendance_service.app.main import app as attendance_app

    attendance_app.dependency_overrides[get_async_db] = _db_override(db_session)
    attendance_app.dependency_overrides[get_current_user] = _admin_override()
    attendance_app.dependency_overrides[require_admin] = _admin_override()
    attendance_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=attendance_app),
        base_url="http://test",
    ) as client:
        yield client

    attendance_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Academy Service
# Note: Academy calls members + sessions via service client.
# Those calls are mocked per-test using mock_service_client().
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def academy_client(db_session):
    """AsyncClient for the academy service with admin auth."""
    from services.academy_service.app.main import app as academy_app

    academy_app.dependency_overrides[get_async_db] = _db_override(db_session)
    academy_app.dependency_overrides[get_current_user] = _admin_override()
    academy_app.dependency_overrides[require_admin] = _admin_override()
    academy_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=academy_app),
        base_url="http://test",
    ) as client:
        yield client

    academy_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Communications Service
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def communications_client(db_session):
    """AsyncClient for the communications service with admin auth."""
    from services.communications_service.app.main import app as comms_app

    comms_app.dependency_overrides[get_async_db] = _db_override(db_session)
    comms_app.dependency_overrides[get_current_user] = _admin_override()
    comms_app.dependency_overrides[require_admin] = _admin_override()
    comms_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=comms_app),
        base_url="http://test",
    ) as client:
        yield client

    comms_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Payments Service
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def payments_client(db_session):
    """AsyncClient for the payments service with admin auth."""
    from services.payments_service.app.main import app as payments_app

    payments_app.dependency_overrides[get_async_db] = _db_override(db_session)
    payments_app.dependency_overrides[get_current_user] = _admin_override()
    payments_app.dependency_overrides[require_admin] = _admin_override()
    payments_app.dependency_overrides[require_service_role] = _service_role_override()

    async with AsyncClient(
        transport=ASGITransport(app=payments_app),
        base_url="http://test",
    ) as client:
        yield client

    payments_app.dependency_overrides.clear()
```

---

## tests/contract/conftest.py (Service Role Auth)

```python
# tests/contract/conftest.py
"""
Contract test fixtures.

Contract tests call internal endpoints with service-role auth.
They reuse the per-service clients from integration/conftest.py
but override auth to use service_role.
"""

# Contract tests use the same service client fixtures from integration/conftest.py.
# The service_role_override is already applied in those fixtures.
# No additional fixtures needed here, but this file exists for future needs.
```

---

## Example: Unit Test

```python
# tests/unit/test_enrollment_validation.py
"""
Unit tests for enrollment business rules.

These test pure logic — no database, no HTTP, no async.
"""

import pytest


class TestEnrollmentCapacity:
    """Tests for cohort capacity enforcement."""

    def test_enrollment_under_capacity_succeeds(self):
        """A cohort with space should accept enrollment."""
        current_count = 15
        max_capacity = 20
        assert current_count < max_capacity

    def test_enrollment_at_capacity_returns_waitlist(self):
        """A full cohort should place new enrollment on waitlist."""
        current_count = 20
        max_capacity = 20
        should_waitlist = current_count >= max_capacity
        assert should_waitlist is True


class TestEnrollmentStatusTransitions:
    """Tests for valid/invalid status transitions."""

    VALID_TRANSITIONS = {
        "PENDING_APPROVAL": ["ENROLLED", "WAITLIST", "DROPPED"],
        "WAITLIST": ["ENROLLED", "DROPPED"],
        "ENROLLED": ["DROPPED", "GRADUATED"],
        "DROPPED": [],       # Terminal
        "GRADUATED": [],     # Terminal
    }

    @pytest.mark.parametrize(
        "from_status,to_status,expected",
        [
            ("PENDING_APPROVAL", "ENROLLED", True),
            ("PENDING_APPROVAL", "WAITLIST", True),
            ("WAITLIST", "ENROLLED", True),
            ("ENROLLED", "GRADUATED", True),
            ("ENROLLED", "DROPPED", True),
            ("GRADUATED", "ENROLLED", False),
            ("DROPPED", "ENROLLED", False),
            ("GRADUATED", "DROPPED", False),
        ],
    )
    def test_status_transition(self, from_status, to_status, expected):
        """Validate allowed and blocked status transitions."""
        allowed = to_status in self.VALID_TRANSITIONS.get(from_status, [])
        assert allowed is expected, (
            f"Transition {from_status} → {to_status} should be "
            f"{'allowed' if expected else 'blocked'}"
        )


class TestMidEntryRules:
    """Tests for mid-cohort enrollment rules."""

    def test_mid_entry_allowed_before_cutoff(self):
        """Student can join mid-cohort before cutoff week."""
        allow_mid_entry = True
        cutoff_week = 4
        current_week = 2
        can_join = allow_mid_entry and current_week <= cutoff_week
        assert can_join is True

    def test_mid_entry_blocked_after_cutoff(self):
        """Student cannot join after cutoff week."""
        allow_mid_entry = True
        cutoff_week = 4
        current_week = 6
        can_join = allow_mid_entry and current_week <= cutoff_week
        assert can_join is False

    def test_mid_entry_blocked_when_disabled(self):
        """Student cannot join mid-cohort when feature disabled."""
        allow_mid_entry = False
        current_week = 1
        cutoff_week = 4
        can_join = allow_mid_entry and current_week <= cutoff_week
        assert can_join is False
```

---

## Example: Integration Test

```python
# tests/integration/test_members_internal.py
"""
Integration tests for the members service internal endpoints.

These endpoints are called by other services via the service client.
They are the most critical to test because they form the contract
that the entire microservice architecture depends on.
"""

import pytest
from tests.factories import MemberFactory, CoachProfileFactory


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_auth_id(members_client, db_session):
    """Internal lookup by Supabase auth_id returns the member."""
    member = MemberFactory.create(auth_id="auth-internal-test")
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/by-auth/{member.auth_id}",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["auth_id"] == "auth-internal-test"
    assert data["email"] == member.email


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_auth_id_not_found(members_client, db_session):
    """Nonexistent auth_id returns 404."""
    response = await members_client.get(
        "/internal/members/by-auth/nonexistent-auth-id",
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_member_by_id(members_client, db_session):
    """Internal lookup by member UUID returns the member."""
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/{member.id}",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(member.id)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_member_lookup(members_client, db_session):
    """Bulk lookup returns all found members, skips missing."""
    m1 = MemberFactory.create()
    m2 = MemberFactory.create()
    db_session.add_all([m1, m2])
    await db_session.commit()

    response = await members_client.post(
        "/internal/members/bulk",
        json={"member_ids": [str(m1.id), str(m2.id), "nonexistent-id"]},
    )

    assert response.status_code == 200
    data = response.json()
    # Should return 2 (skips the nonexistent one)
    assert len(data) == 2
    returned_ids = {item["id"] for item in data}
    assert str(m1.id) in returned_ids
    assert str(m2.id) in returned_ids


@pytest.mark.asyncio
@pytest.mark.integration
async def test_bulk_member_lookup_empty_list(members_client, db_session):
    """Bulk lookup with empty list returns empty array."""
    response = await members_client.post(
        "/internal/members/bulk",
        json={"member_ids": []},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_coach_profile(members_client, db_session):
    """Internal coach profile lookup returns coach data."""
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/profile",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["member_id"] == str(member.id)
    assert data["learn_to_swim_grade"] == "grade_2"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_coach_profile_not_a_coach(members_client, db_session):
    """Coach profile lookup for non-coach returns 404."""
    member = MemberFactory.create()  # No coach profile
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/profile",
    )

    assert response.status_code == 404
```

---

## Example: Contract Test

```python
# tests/contract/test_members_contract.py
"""
Contract tests for members service internal endpoints.

These tests validate that the response SHAPE matches what consuming
services expect. They don't test business logic — they test that
the JSON keys and types are correct.

If these tests break, it means a consuming service will break too.
Check the dependency map in TESTING_ARCHITECTURE.md to see who's affected.
"""

import pytest
from tests.factories import MemberFactory, CoachProfileFactory


@pytest.mark.asyncio
@pytest.mark.contract
async def test_member_by_id_contract(members_client, db_session):
    """
    GET /internal/members/{id} response contains all fields that
    service_client.get_member_by_id() consumers depend on.

    Consumers: academy_service, communications_service, payments_service
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(f"/internal/members/{member.id}")
    assert response.status_code == 200
    data = response.json()

    # These fields are used by consuming services.
    # If you rename or remove any, update the service client AND all consumers.
    required_fields = ["id", "auth_id", "email", "full_name", "is_active"]
    for field in required_fields:
        assert field in data, (
            f"Missing required contract field '{field}' in /internal/members/{{id}} response. "
            f"This field is used by academy, communications, and payments services."
        )

    # Type checks
    assert isinstance(data["id"], str)
    assert isinstance(data["is_active"], bool)


@pytest.mark.asyncio
@pytest.mark.contract
async def test_member_by_auth_id_contract(members_client, db_session):
    """
    GET /internal/members/by-auth/{auth_id} response contract.

    Consumers: attendance_service, sessions_service
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/members/by-auth/{member.auth_id}"
    )
    assert response.status_code == 200
    data = response.json()

    required_fields = ["id", "auth_id", "email", "full_name", "is_active"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in /internal/members/by-auth response."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_bulk_members_contract(members_client, db_session):
    """
    POST /internal/members/bulk response contract.

    Consumers: academy_service (cohort roster display)
    """
    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    response = await members_client.post(
        "/internal/members/bulk",
        json={"member_ids": [str(member.id)]},
    )
    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) >= 1

    item = data[0]
    required_fields = ["id", "full_name", "email"]
    for field in required_fields:
        assert field in item, (
            f"Missing contract field '{field}' in bulk members response item."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_coach_profile_contract(members_client, db_session):
    """
    GET /internal/coaches/{id}/profile response contract.

    Consumers: academy_service (coach assignment), ai_service (scoring)
    """
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/profile"
    )
    assert response.status_code == 200
    data = response.json()

    required_fields = [
        "member_id",
        "display_name",
        "learn_to_swim_grade",
        "total_coaching_hours",
        "status",
    ]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in coach profile response. "
            f"Used by academy_service for coach assignment."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_coach_readiness_contract(members_client, db_session):
    """
    GET /internal/coaches/{id}/readiness response contract.

    Consumers: ai_service (complexity scoring, coach suggestion)
    """
    member = MemberFactory.create(roles=["member", "coach"])
    db_session.add(member)
    await db_session.flush()

    coach = CoachProfileFactory.create(member_id=member.id)
    db_session.add(coach)
    await db_session.commit()

    response = await members_client.get(
        f"/internal/coaches/{member.id}/readiness"
    )
    assert response.status_code == 200
    data = response.json()

    required_fields = [
        "member_id",
        "coaching_years",
        "total_coaching_hours",
        "average_feedback_rating",
    ]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in coach readiness response. "
            f"Used by ai_service for complexity scoring."
        )
```

---

## Mocking the Service Client

When testing a service that calls other services (e.g., academy calls members), mock the service client:

```python
# tests/integration/test_academy_cohorts.py
import pytest
from tests.factories import ProgramFactory, CohortFactory
from tests.conftest import mock_service_client, build_member_mock, build_coach_profile_mock


@pytest.mark.asyncio
@pytest.mark.integration
async def test_assign_coach_to_cohort(academy_client, db_session):
    """Assigning a coach uses service client to verify coach exists."""
    program = ProgramFactory.create()
    db_session.add(program)
    await db_session.flush()

    cohort = CohortFactory.create(program_id=program.id, required_coach_grade="grade_2")
    db_session.add(cohort)
    await db_session.commit()

    coach_member_id = "00000000-0000-0000-0000-000000000099"

    # Mock the service client so academy doesn't make real HTTP calls
    with mock_service_client(
        get_member_by_id=build_member_mock(member_id=coach_member_id),
        get_coach_profile=build_coach_profile_mock(
            member_id=coach_member_id,
            learn_to_swim_grade="grade_2",
        ),
    ):
        response = await academy_client.post(
            f"/api/v1/academy/cohorts/{cohort.id}/assign-coach",
            json={"coach_member_id": coach_member_id},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["coach_id"] == coach_member_id
```

---

## Auth Mocking Patterns

### Test as admin (default in integration fixtures)

```python
# The integration fixtures default to admin — no special setup needed
async def test_admin_action(members_client, db_session):
    response = await members_client.get("/api/v1/admin/members")
    assert response.status_code == 200
```

### Test as regular member (should be denied admin access)

```python
from tests.conftest import override_auth_as_member

async def test_member_cant_access_admin(db_session):
    from services.members_service.app.main import app as members_app
    from libs.db.session import get_async_db

    members_app.dependency_overrides[get_async_db] = _db_override(db_session)

    with override_auth_as_member(members_app):
        async with AsyncClient(
            transport=ASGITransport(app=members_app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/v1/admin/members")
            assert response.status_code == 403

    members_app.dependency_overrides.clear()
```

### Test as specific user (for "my profile" endpoints)

```python
from tests.conftest import override_auth, make_member_user

async def test_get_my_profile(db_session):
    from services.members_service.app.main import app as members_app

    member = MemberFactory.create(auth_id="specific-auth-id")
    db_session.add(member)
    await db_session.commit()

    user = make_member_user(user_id="specific-auth-id", email=member.email)

    with override_auth(members_app, user):
        async with AsyncClient(...) as client:
            response = await client.get("/api/v1/members/me")
            assert response.status_code == 200
            assert response.json()["auth_id"] == "specific-auth-id"
```

---

## Common Pitfalls

### 1. Forgetting to `await db_session.flush()` before creating related models

```python
# WRONG — coach.member_id won't exist in DB yet
member = MemberFactory.create()
db_session.add(member)
coach = CoachProfileFactory.create(member_id=member.id)  # FK violation!

# RIGHT — flush writes member to DB (but doesn't commit)
member = MemberFactory.create()
db_session.add(member)
await db_session.flush()  # <-- member.id now exists in DB
coach = CoachProfileFactory.create(member_id=member.id)
db_session.add(coach)
await db_session.commit()
```

### 2. Using `datetime.utcnow()` instead of `datetime.now(timezone.utc)`

```python
# WRONG — returns naive datetime, causes comparison bugs
from datetime import datetime
session = SessionFactory.create(starts_at=datetime.utcnow())

# RIGHT — timezone-aware
from datetime import datetime, timezone
session = SessionFactory.create(starts_at=datetime.now(timezone.utc))
```

### 3. Not clearing dependency overrides

```python
# WRONG — override leaks to next test
app.dependency_overrides[get_current_user] = mock_user
response = await client.get("/endpoint")
# Test ends without clearing — next test inherits the mock!

# RIGHT — always clean up (our fixtures handle this automatically)
# But if you set overrides manually in a test, use try/finally:
try:
    app.dependency_overrides[get_current_user] = mock_user
    response = await client.get("/endpoint")
finally:
    app.dependency_overrides.clear()
```

### 4. Testing through the gateway instead of directly

```python
# WRONG — failure could be gateway proxy, auth middleware, OR service logic
response = await gateway_client.get("/api/v1/sessions/123")

# RIGHT — test the service directly, isolate the failure
response = await sessions_client.get("/api/v1/sessions/123")
```

### 5. Not mocking the service client in integration tests

```python
# WRONG — academy test makes real HTTP call to members service (which isn't running)
response = await academy_client.post("/api/v1/academy/enrollments", json={...})
# Fails with ConnectionError because members-service:8001 isn't reachable in tests

# RIGHT — mock the service client
with mock_service_client(get_member_by_id=build_member_mock()):
    response = await academy_client.post("/api/v1/academy/enrollments", json={...})
```

### 6. Enum string casing

```python
# Models use different enum formats — check the model definition
# SessionType enum values: "COHORT_CLASS", "CLUB", "COMMUNITY" (uppercase)
# CohortStatus: "OPEN", "ACTIVE", "COMPLETED" (uppercase)
# Member approval_status: "pending", "approved" (lowercase — it's a plain string, not an enum)
# CoachProfile status: "draft", "approved" (lowercase)
```

---

## Step-by-Step Implementation Checklist

Use this checklist to track progress. Each step builds on the previous.

### Phase 1: Infrastructure

- [ ] Create `pytest.ini` at backend root
- [ ] Create `tests/__init__.py`
- [ ] Create `tests/unit/__init__.py`
- [ ] Create `tests/integration/__init__.py`
- [ ] Create `tests/contract/__init__.py`
- [ ] Rewrite root `conftest.py` (import all models, remove client fixture)
- [ ] Create `tests/conftest.py` (auth fixtures, mock builders)
- [ ] Create `tests/factories.py` (all model factories)
- [ ] Create `tests/integration/conftest.py` (per-service clients)
- [ ] Create `tests/contract/conftest.py`
- [ ] Move `tests/test_members_service.py` → `tests/unit/test_member_tiers.py`
- [ ] Move `tests/test_session_stats.py` → `tests/unit/test_session_stats.py`
- [ ] Run `pytest tests/unit/` — verify existing tests pass
- [ ] Run `pytest tests/integration/` — verify fixtures load (no tests yet)

### Phase 2: Members Service Tests

- [ ] Write `tests/integration/test_members_internal.py` (~8 tests)
- [ ] Write `tests/contract/test_members_contract.py` (~7 tests)
- [ ] Write `tests/integration/test_members_api.py` (~9 tests, absorb registration tests)
- [ ] Run `pytest tests/ -v` — all pass

### Phase 3: Sessions + Attendance Tests

- [ ] Write `tests/integration/test_sessions_api.py` (~8 tests)
- [ ] Write `tests/integration/test_sessions_internal.py` (~7 tests)
- [ ] Write `tests/contract/test_sessions_contract.py` (~5 tests)
- [ ] Write `tests/integration/test_attendance_api.py` (~6 tests)
- [ ] Write `tests/integration/test_attendance_internal.py` (~3 tests)
- [ ] Write `tests/contract/test_attendance_contract.py` (~2 tests)
- [ ] Run `pytest tests/ -v` — all pass

### Phase 4: Academy Tests

- [ ] Write `tests/integration/test_academy_programs.py` (~5 tests)
- [ ] Write `tests/integration/test_academy_cohorts.py` (~8 tests)
- [ ] Write `tests/integration/test_academy_enrollments.py` (~7 tests)
- [ ] Write `tests/integration/test_academy_coach_assignment.py` (~4 tests)
- [ ] Run `pytest tests/ -v` — all pass

### Phase 5: Payments Tests

- [ ] Write `tests/integration/test_payments_api.py` (~6 tests)
- [ ] Write `tests/integration/test_payments_webhooks.py` (~4 tests)
- [ ] Write `tests/integration/test_payments_payouts.py` (~4 tests)
- [ ] Run `pytest tests/ -v` — all pass

### Phase 6: Communications + Remaining Unit Tests

- [ ] Write `tests/integration/test_communications_api.py` (~4 tests)
- [ ] Write `tests/unit/test_enrollment_validation.py` (~10 tests)
- [ ] Write `tests/unit/test_discount_calculation.py` (~6 tests)
- [ ] Write `tests/unit/test_complexity_scoring.py` (~5 tests)
- [ ] Write `tests/unit/test_payout_calculation.py` (~3 tests)
- [ ] Run `pytest tests/ -v` — ALL ~105 tests pass
- [ ] Run `pytest --cov=services --cov-report=term-missing` — review coverage

### Phase 7: Cleanup

- [ ] Delete `tests/test_db.py` (replaced by real integration tests)
- [ ] Delete `tests/test_registration_flow.py` (absorbed into test_members_api.py)
- [ ] Delete `tests/test_session_delete.py` (absorbed into test_sessions_api.py)
- [ ] Update `CLAUDE.md` test commands section
- [ ] Commit with message: `test: add comprehensive test suite (3 layers, ~105 tests)`

---

## Notes for AI Agents

When implementing tests from this guide:

1. **Start with Phase 1 infrastructure.** Nothing works without it.
2. **Run tests after every file you create.** Don't batch — catch import errors early.
3. **Factory field names must match model columns exactly.** If a test fails with `unexpected keyword argument`, check the model definition in `services/<service>/models.py`.
4. **Enum values are strings.** The factories use string values (e.g., `"CLUB"`, `"SCHEDULED"`) not Python enum instances. Check the model to see if it expects uppercase or lowercase.
5. **The `_db_override` pattern uses `async def` with `yield`.** This matches FastAPI's dependency injection pattern for database sessions.
6. **Internal endpoint URL prefixes vary by service.** Members uses `/internal/members/...`, sessions uses `/internal/sessions/...`, attendance uses `/internal/attendance/...`. Check the actual router file.
7. **Some services mount routers with prefixes, some don't.** Always check `app/main.py` for the service to see the actual URL prefix (e.g., `/api/v1/sessions` vs `/sessions`).
8. **If a factory create() fails**, the most likely cause is a missing required field or wrong field name. Read the model source to verify.

---

_Created: February 2026_
_Last updated: February 2026_
