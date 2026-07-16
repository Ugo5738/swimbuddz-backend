"""Integration tests for the public attendance sign-in endpoint (walk-in path).

Covers the fix where the public endpoint links a CONFIRMED SessionBooking and
trusts it for access — so admin walk-ins (which bypass tier checks at booking
time) still get a PRESENT attendance row, count in reports, and aren't swept
ABSENT overnight.

Cross-service calls are patched at the module where they're *used*
(``...sign_in.<name>``) — patching ``libs.common.service_client`` would not
affect the names already bound via ``from ... import ...``.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import services.attendance_service.routers.member.sign_in as signin_mod
from libs.auth.dependencies import require_coach
from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from services.attendance_service.schemas import AttendanceCreate
from tests.conftest import make_member_user, override_auth_as_member


def _session_payload(session_id: uuid.UUID) -> dict:
    return {
        "id": str(session_id),
        "title": "Week 6 - Beginner Freestyle",
        "session_type": "cohort_class",
        "cohort_id": str(uuid.uuid4()),
        "starts_at": "2026-05-23T11:00:00+00:00",
        "ends_at": "2026-05-23T12:00:00+00:00",
        "pool_fee": 350000,
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_signin_rejects_ordinary_member(attendance_client):
    """The operator walk-in route must remain unavailable to ordinary members."""
    from services.attendance_service.app.main import app as attendance_app

    # The shared client fixture normally overrides require_coach with an admin.
    # Remove that shortcut so this test exercises the real dependency against
    # an authenticated member.
    attendance_app.dependency_overrides.pop(require_coach, None)
    with override_auth_as_member(attendance_app, make_member_user()):
        response = await attendance_client.post(
            f"/attendance/sessions/{uuid.uuid4()}/attendance/public",
            json={
                "member_id": str(uuid.uuid4()),
                "status": "present",
                "role": "swimmer",
            },
        )

    assert response.status_code == 403, response.text
    assert "coach" in response.json()["detail"].lower()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_signin_links_booking_and_skips_tier_check(
    attendance_client, db_session, monkeypatch, seed_member_row
):
    """A CONFIRMED booking → PRESENT row linked to the booking, tier check skipped."""
    member = await seed_member_row()
    await db_session.commit()

    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()

    async def fake_get_session(*args, **kwargs):
        return _session_payload(session_id)

    async def fake_get_booking(*args, **kwargs):
        return {"id": str(booking_id)}

    async def must_not_run(*args, **kwargs):
        raise AssertionError(
            "validate_session_access must be skipped when a confirmed booking exists"
        )

    monkeypatch.setattr(signin_mod, "get_session_by_id", fake_get_session)
    monkeypatch.setattr(
        signin_mod, "get_confirmed_booking_for_session_member", fake_get_booking
    )
    monkeypatch.setattr(signin_mod, "validate_session_access", must_not_run)

    resp = await attendance_client.post(
        f"/attendance/sessions/{session_id}/attendance/public",
        json={"member_id": str(member.id), "status": "present", "role": "swimmer"},
    )

    assert resp.status_code == 200, resp.text
    record = (
        await db_session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == session_id,
                AttendanceRecord.member_id == member.id,
            )
        )
    ).scalar_one()
    assert record.booking_id == booking_id
    assert (
        getattr(record.status, "value", record.status) == AttendanceStatus.PRESENT.value
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_public_signin_allows_controlled_walkin_without_booking(
    attendance_client,
    db_session,
    monkeypatch,
    seed_member_row,
):
    """An authorized coach/admin can record a genuine walk-in without a booking."""
    member = await seed_member_row()
    await db_session.commit()

    session_id = uuid.uuid4()
    session_payload = _session_payload(session_id)

    get_session = AsyncMock(return_value=session_payload)
    get_booking = AsyncMock(return_value=None)
    validate_access = AsyncMock(return_value=None)

    monkeypatch.setattr(signin_mod, "get_session_by_id", get_session)
    monkeypatch.setattr(
        signin_mod,
        "get_confirmed_booking_for_session_member",
        get_booking,
    )
    monkeypatch.setattr(
        signin_mod,
        "validate_session_access",
        validate_access,
    )

    resp = await attendance_client.post(
        f"/attendance/sessions/{session_id}/attendance/public",
        json={
            "member_id": str(member.id),
            "status": "present",
            "role": "swimmer",
        },
    )

    assert resp.status_code == 200, resp.text

    record = (
        await db_session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == session_id,
                AttendanceRecord.member_id == member.id,
            )
        )
    ).scalar_one()

    assert record.booking_id is None
    assert (
        getattr(record.status, "value", record.status) == AttendanceStatus.PRESENT.value
    )

    validate_access.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_authenticated_signin_links_booking_and_uses_booking_override(
    db_session,
    monkeypatch,
    seed_member_row,
):
    """A confirmed booking bypasses tier checks but still runs shared validation."""
    member = await seed_member_row()
    await db_session.commit()

    session_id = uuid.uuid4()
    booking_id = uuid.uuid4()
    session_payload = _session_payload(session_id)

    get_session = AsyncMock(return_value=session_payload)
    get_booking = AsyncMock(
        return_value={
            "id": str(booking_id),
            "status": "confirmed",
        }
    )
    validate_access = AsyncMock(return_value=None)

    monkeypatch.setattr(
        signin_mod,
        "get_session_by_id",
        get_session,
    )
    monkeypatch.setattr(
        signin_mod,
        "get_confirmed_booking_for_session_member",
        get_booking,
    )
    monkeypatch.setattr(
        signin_mod,
        "validate_session_access",
        validate_access,
    )
    monkeypatch.setattr(
        signin_mod,
        "_check_attendance_milestones",
        AsyncMock(return_value=None),
    )

    attendance = await signin_mod.sign_in_to_session(
        session_id=session_id,
        attendance_in=AttendanceCreate(),
        current_member=member,
        db=db_session,
    )

    assert attendance.booking_id == booking_id

    validate_access.assert_awaited_once_with(
        session_payload,
        str(member.id),
        confirmed_booking=True,
    )
