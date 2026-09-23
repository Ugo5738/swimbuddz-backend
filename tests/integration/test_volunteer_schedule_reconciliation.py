"""Volunteer shifts preserve their relationship to a rescheduled Session."""

import uuid
from datetime import date, datetime, time, timezone

import pytest

from services.volunteer_service.models import OpportunityStatus, VolunteerOpportunity


def _opportunity(
    *,
    session_id: uuid.UUID,
    title: str,
    opportunity_date: date,
    start_time: time | None,
    end_time: time | None,
    location_name: str | None,
    status: OpportunityStatus = OpportunityStatus.OPEN,
) -> VolunteerOpportunity:
    return VolunteerOpportunity(
        title=title,
        date=opportunity_date,
        start_time=start_time,
        end_time=end_time,
        session_id=session_id,
        location_name=location_name,
        slots_needed=1,
        slots_filled=0,
        status=status,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_reschedule_preserves_shift_offsets_and_custom_venues(
    volunteer_client, db_session
):
    session_id = uuid.uuid4()
    old_start = datetime(2026, 10, 3, 8, tzinfo=timezone.utc)  # 09:00 Lagos
    old_end = datetime(2026, 10, 3, 10, tzinfo=timezone.utc)
    new_start = datetime(2026, 10, 4, 10, tzinfo=timezone.utc)  # 11:00 Lagos
    new_end = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)

    inherited = _opportunity(
        session_id=session_id,
        title="Inherited venue",
        opportunity_date=date(2026, 10, 3),
        start_time=time(8, 30),
        end_time=time(9, 15),
        location_name="Old Pool",
    )
    custom = _opportunity(
        session_id=session_id,
        title="Custom venue",
        opportunity_date=date(2026, 10, 3),
        start_time=time(10, 0),
        end_time=time(12, 0),
        location_name="Car park meeting point",
    )
    untimed = _opportunity(
        session_id=session_id,
        title="Untimed shift",
        opportunity_date=date(2026, 10, 3),
        start_time=None,
        end_time=None,
        location_name="Old Pool",
    )
    overnight = _opportunity(
        session_id=session_id,
        title="Overnight shift",
        opportunity_date=date(2026, 10, 3),
        start_time=time(23, 30),
        end_time=time(0, 30),
        location_name="Old Pool",
    )
    completed = _opportunity(
        session_id=session_id,
        title="Completed",
        opportunity_date=date(2026, 10, 3),
        start_time=time(9, 0),
        end_time=time(10, 0),
        location_name="Old Pool",
        status=OpportunityStatus.COMPLETED,
    )
    cancelled = _opportunity(
        session_id=session_id,
        title="Cancelled",
        opportunity_date=date(2026, 10, 3),
        start_time=time(9, 0),
        end_time=time(10, 0),
        location_name="Old Pool",
        status=OpportunityStatus.CANCELLED,
    )
    db_session.add_all([inherited, custom, untimed, overnight, completed, cancelled])
    await db_session.commit()

    payload = {
        "old_starts_at": old_start.isoformat(),
        "old_ends_at": old_end.isoformat(),
        "new_starts_at": new_start.isoformat(),
        "new_ends_at": new_end.isoformat(),
        "old_timezone": "Africa/Lagos",
        "new_timezone": "Africa/Lagos",
        "old_location_name": "Old Pool",
        "new_location_name": "New Pool",
    }
    response = await volunteer_client.patch(
        f"/internal/volunteer/sessions/{session_id}/schedule",
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 4}
    for opportunity in [inherited, custom, untimed, overnight, completed, cancelled]:
        await db_session.refresh(opportunity)

    assert (inherited.date, inherited.start_time, inherited.end_time) == (
        date(2026, 10, 4),
        time(10, 30),
        time(11, 15),
    )
    assert inherited.location_name == "New Pool"
    assert (custom.date, custom.start_time, custom.end_time) == (
        date(2026, 10, 4),
        time(12, 0),
        time(14, 0),
    )
    assert custom.location_name == "Car park meeting point"
    assert (untimed.date, untimed.start_time, untimed.end_time) == (
        date(2026, 10, 4),
        None,
        None,
    )
    assert untimed.location_name == "New Pool"
    assert (overnight.date, overnight.start_time, overnight.end_time) == (
        date(2026, 10, 5),
        time(1, 30),
        time(2, 30),
    )
    assert completed.date == date(2026, 10, 3)
    assert completed.location_name == "Old Pool"
    assert cancelled.date == date(2026, 10, 3)
    assert cancelled.location_name == "Old Pool"

    retry = await volunteer_client.patch(
        f"/internal/volunteer/sessions/{session_id}/schedule",
        json=payload,
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"updated": 0}
    await db_session.refresh(inherited)
    assert (inherited.date, inherited.start_time, inherited.end_time) == (
        date(2026, 10, 4),
        time(10, 30),
        time(11, 15),
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_session_reschedule_rejects_unknown_timezone(volunteer_client):
    starts_at = datetime(2026, 10, 3, 8, tzinfo=timezone.utc)
    response = await volunteer_client.patch(
        f"/internal/volunteer/sessions/{uuid.uuid4()}/schedule",
        json={
            "old_starts_at": starts_at.isoformat(),
            "old_ends_at": starts_at.replace(hour=10).isoformat(),
            "new_starts_at": starts_at.isoformat(),
            "new_ends_at": starts_at.replace(hour=10).isoformat(),
            "old_timezone": "Not/A-Timezone",
            "new_timezone": "Africa/Lagos",
        },
    )

    assert response.status_code == 422
    assert "Unknown timezone" in response.json()["detail"]
