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

import pytest
from sqlalchemy import select

import services.attendance_service.routers.member.sign_in as signin_mod
from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from tests.factories import MemberFactory


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
async def test_public_signin_links_booking_and_skips_tier_check(
    attendance_client, db_session, monkeypatch
):
    """A CONFIRMED booking → PRESENT row linked to the booking, tier check skipped."""
    member = MemberFactory.create()
    db_session.add(member)
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
async def test_public_signin_enforces_tier_check_without_booking(
    attendance_client, db_session, monkeypatch
):
    """No booking → the tier check still runs and its rejection is surfaced."""
    from fastapi import HTTPException

    member = MemberFactory.create()
    db_session.add(member)
    await db_session.commit()

    session_id = uuid.uuid4()

    async def fake_get_session(*args, **kwargs):
        return _session_payload(session_id)

    async def no_booking(*args, **kwargs):
        return None

    async def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="not enrolled")

    monkeypatch.setattr(signin_mod, "get_session_by_id", fake_get_session)
    monkeypatch.setattr(
        signin_mod, "get_confirmed_booking_for_session_member", no_booking
    )
    monkeypatch.setattr(signin_mod, "validate_session_access", deny)

    resp = await attendance_client.post(
        f"/attendance/sessions/{session_id}/attendance/public",
        json={"member_id": str(member.id), "status": "present", "role": "swimmer"},
    )

    assert resp.status_code == 403
