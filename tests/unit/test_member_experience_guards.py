"""Enrollment billing, private library visibility, and dated volunteer listings."""

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import sqlite

from services.media_service.models import MediaItem
from services.media_service.routers import vaults
from services.media_service.services.vault_access import VaultActor
from services.members_service.schemas.member import PendingRegistrationCreate
from services.payments_service.routers import internal
from services.payments_service.schemas import InternalInitializeRequest
from services.academy_service.routers.enrollments import admin_payments
from services.academy_service.models import EnrollmentStatus
from services.volunteer_service.routers.member import opportunities


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example",
        "/\\evil.example",
        "/%5cevil.example",
        "/%2fevil.example",
        "https://evil.example",
        "/%00bad",
    ],
)
def test_registration_rejects_nonlocal_return_paths(path):
    with pytest.raises(ValidationError):
        PendingRegistrationCreate(
            email="learner@example.com",
            first_name="Test",
            last_name="Learner",
            return_to=path,
        )


def test_registration_keeps_selected_cohort():
    path = f"/account/academy/cohorts/{uuid4()}"
    payload = PendingRegistrationCreate(
        email="learner@example.com",
        first_name="Test",
        last_name="Learner",
        return_to=path,
    )
    assert payload.model_dump()["return_to"] == path


@pytest.mark.asyncio
async def test_internal_billing_cannot_bypass_waitlist_check(monkeypatch):
    monkeypatch.setattr(internal, "_paystack_enabled", lambda: True)
    quote = AsyncMock(side_effect=HTTPException(409, "waitlisted"))
    monkeypatch.setattr(internal, "academy_payment_context", quote)
    provider = Mock()
    monkeypatch.setattr(internal.httpx, "AsyncClient", provider)
    db = SimpleNamespace(execute=AsyncMock(), add=Mock(), commit=AsyncMock())
    enrollment_id = uuid4()
    request = InternalInitializeRequest(
        purpose="academy_cohort",
        reference="test-waitlist",
        amount=50000,
        member_auth_id=str(uuid4()),
        metadata={"enrollment_id": str(enrollment_id)},
    )
    with pytest.raises(HTTPException) as raised:
        await internal.internal_initialize_payment(request, db=db)
    assert raised.value.status_code == 409
    quote.assert_awaited_once_with(
        enrollment_id=enrollment_id,
        member_auth_id=request.member_auth_id,
        use_installments=False,
    )
    provider.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_billing_rejects_a_stale_installment(monkeypatch):
    monkeypatch.setattr(internal, "_paystack_enabled", lambda: True)
    monkeypatch.setattr(
        internal,
        "academy_payment_context",
        AsyncMock(return_value={"installment_id": str(uuid4())}),
    )
    provider = Mock()
    monkeypatch.setattr(internal.httpx, "AsyncClient", provider)
    request = InternalInitializeRequest(
        purpose="academy_cohort",
        reference="test-stale",
        amount=50000,
        member_auth_id=str(uuid4()),
        metadata={"enrollment_id": str(uuid4()), "installment_id": str(uuid4())},
    )
    with pytest.raises(HTTPException) as raised:
        await internal.internal_initialize_payment(request, db=SimpleNamespace())
    assert raised.value.status_code == 409
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_waitlisted_payment_does_not_activate_enrollment(monkeypatch):
    enrollment = SimpleNamespace(status=EnrollmentStatus.WAITLIST)
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: enrollment)
        )
    )
    sync = AsyncMock()
    monkeypatch.setattr(admin_payments, "_sync_installment_state_for_enrollment", sync)
    with pytest.raises(HTTPException) as raised:
        await admin_payments.admin_mark_enrollment_paid(uuid4(), payload=None, db=db)
    assert raised.value.status_code == 409
    sync.assert_not_awaited()


def test_library_visibility_with_real_query_execution(monkeypatch):
    """Execute the portable access predicate against actual rows, including expired grants."""
    now = datetime(2026, 9, 11, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(vaults, "utc_now", lambda: now)
    actor = VaultActor(auth_id=uuid4(), member_id=uuid4(), is_admin=False)
    other = uuid4()
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE media_items (id TEXT, vault_id TEXT, soft_deleted_at TEXT, processing_status TEXT, uploaded_by TEXT);
        CREATE TABLE media_vault_grants (id TEXT, vault_id TEXT, member_id TEXT, revoked_at TEXT, starts_at TEXT, expires_at TEXT, role TEXT);
    """)
    expected = []
    for role, state in [
        ("contributor", "current"),
        ("curator", "current"),
        ("curator", "expired"),
        ("curator", "revoked"),
        ("curator", "future"),
        ("curator", "other-member"),
    ]:
        vault_id = uuid4().hex
        conn.execute(
            "INSERT INTO media_vault_grants VALUES (?,?,?,?,?,?,?)",
            (
                uuid4().hex,
                vault_id,
                other.hex if state == "other-member" else actor.member_id.hex,
                "2026-09-01" if state == "revoked" else None,
                "2027-01-01" if state == "future" else "2026-01-01",
                "2026-09-01" if state == "expired" else "2027-01-01",
                role,
            ),
        )
        for uploader in [actor.auth_id, other]:
            item_id = uuid4().hex
            conn.execute(
                "INSERT INTO media_items VALUES (?,?,?,?,?)",
                (item_id, vault_id, None, "ready", uploader.hex),
            )
            if state == "current" and (role == "curator" or uploader == actor.auth_id):
                expected.append(item_id)
    for deleted, processing, vault_id in [
        ("2026-09-10", "ready", uuid4().hex),
        (None, "uploading", uuid4().hex),
        (None, "ready", None),
    ]:
        conn.execute(
            "INSERT INTO media_items VALUES (?,?,?,?,?)",
            (uuid4().hex, vault_id, deleted, processing, actor.auth_id.hex),
        )

    def visible(current_actor):
        query = select(MediaItem.id).where(
            *vaults._library_access_conditions(current_actor)
        )
        sql = str(
            query.compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        return {row[0] for row in conn.execute(sql)}

    assert visible(actor) == set(expected)
    assert visible(VaultActor(actor.auth_id, None, False)) == set()
    assert len(visible(VaultActor(actor.auth_id, None, True))) == 12
    conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period,expected",
    [("upcoming", ["future", "today"]), ("past", ["old-open", "old-complete"])],
)
async def test_opportunity_period_filters_old_open_records(
    monkeypatch, period, expected
):
    monkeypatch.setattr(
        opportunities, "utc_now", lambda: datetime(2026, 9, 11, 10, tzinfo=timezone.utc)
    )
    # Execute the actual date/status WHERE clause against a small deterministic dataset.
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE volunteer_opportunities (id TEXT, date TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO volunteer_opportunities VALUES (?,?,?)",
        [
            ("old-open", "2026-09-10", "open"),
            ("old-complete", "2026-09-09", "completed"),
            ("draft", "2026-09-01", "draft"),
            ("today", "2026-09-11", "open"),
            ("future", "2026-09-12", "open"),
        ],
    )

    async def execute(query):
        from services.volunteer_service.models import VolunteerOpportunity

        portable = select(VolunteerOpportunity.id).where(query.whereclause)
        sql = str(
            portable.compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        rows = [row[0] for row in conn.execute(sql)]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    async def enrich(row):
        return row

    monkeypatch.setattr(opportunities, "_enrich_opportunity", enrich)
    result = await opportunities.list_opportunities(
        status_filter=None,
        period=period,
        skip=0,
        limit=50,
        db=SimpleNamespace(execute=execute),
    )
    assert sorted(result) == sorted(expected)
    conn.close()
