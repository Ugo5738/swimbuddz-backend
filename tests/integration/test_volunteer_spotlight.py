"""Integration tests for monthly volunteer spotlight rotation."""

from datetime import date, datetime, timezone

import pytest


async def _seed_member(db, *, first_name="Test", last_name="Volunteer"):
    from tests.factories import MemberFactory

    member = MemberFactory.create(first_name=first_name, last_name=last_name)
    db.add(member)
    await db.flush()
    return member.id


def _profile(member_id, **overrides):
    from services.volunteer_service.models import VolunteerProfile

    defaults = {
        "member_id": member_id,
        "is_active": True,
        "total_hours": 0.0,
    }
    defaults.update(overrides)
    return VolunteerProfile(**defaults)


def _hours(member_id, *, hours, logged_on):
    from services.volunteer_service.models import VolunteerHoursLog

    return VolunteerHoursLog(
        member_id=member_id,
        hours=hours,
        date=logged_on,
        source="manual_entry",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_monthly_spotlight_features_previous_month_top_volunteer(db_session):
    from sqlalchemy import select

    from services.volunteer_service.models import VolunteerProfile
    from services.volunteer_service.services import apply_monthly_volunteer_spotlight

    old_member = await _seed_member(db_session, first_name="Old")
    lower_member = await _seed_member(db_session, first_name="Lower")
    winner_member = await _seed_member(db_session, first_name="Winner")
    outside_period_member = await _seed_member(db_session, first_name="Outside")

    db_session.add_all(
        [
            _profile(old_member, is_featured=True, total_hours=20),
            _profile(lower_member, total_hours=2),
            _profile(winner_member, total_hours=4),
            _profile(outside_period_member, total_hours=100),
            _hours(lower_member, hours=2, logged_on=date(2026, 5, 12)),
            _hours(winner_member, hours=3, logged_on=date(2026, 5, 2)),
            _hours(winner_member, hours=1, logged_on=date(2026, 5, 26)),
            _hours(outside_period_member, hours=50, logged_on=date(2026, 4, 30)),
        ]
    )
    await db_session.commit()

    now = datetime(2026, 6, 1, 0, 10, tzinfo=timezone.utc)
    result = await apply_monthly_volunteer_spotlight(db_session, now=now)

    assert result.period_start == date(2026, 5, 1)
    assert result.period_end == date(2026, 6, 1)
    assert result.member_id == winner_member
    assert result.monthly_hours == 4.0
    assert result.monthly_logs == 2
    assert result.featured_until == datetime(2026, 7, 1, tzinfo=timezone.utc)

    rows = (
        (
            await db_session.execute(
                select(VolunteerProfile).where(
                    VolunteerProfile.member_id.in_([old_member, winner_member])
                )
            )
        )
        .scalars()
        .all()
    )
    by_member = {p.member_id: p for p in rows}
    assert by_member[old_member].is_featured is False
    assert by_member[winner_member].is_featured is True
    assert by_member[winner_member].featured_from == now
    assert by_member[winner_member].featured_until == datetime(
        2026, 7, 1, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_monthly_spotlight_clears_feature_when_month_has_no_hours(db_session):
    from sqlalchemy import select

    from services.volunteer_service.models import VolunteerProfile
    from services.volunteer_service.services import apply_monthly_volunteer_spotlight

    member_id = await _seed_member(db_session)
    db_session.add(_profile(member_id, is_featured=True, total_hours=10))
    await db_session.commit()

    result = await apply_monthly_volunteer_spotlight(
        db_session,
        now=datetime(2026, 6, 1, 0, 10, tzinfo=timezone.utc),
    )

    assert result.member_id is None
    assert result.monthly_hours == 0.0

    profile = (
        await db_session.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one()
    assert profile.is_featured is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_monthly_spotlight_skips_excluded_members_and_coaches(db_session):
    from services.volunteer_service.services import apply_monthly_volunteer_spotlight

    founder = await _seed_member(db_session, first_name="Founder")
    coach = await _seed_member(db_session, first_name="Coach")
    winner = await _seed_member(db_session, first_name="Eligible")

    db_session.add_all(
        [
            _profile(founder, total_hours=50),
            _profile(coach, total_hours=30),
            _profile(winner, total_hours=5),
            _hours(founder, hours=10, logged_on=date(2026, 5, 3)),  # top, but excluded
            _hours(coach, hours=7, logged_on=date(2026, 5, 4)),  # 2nd, but a coach
            _hours(
                winner, hours=3, logged_on=date(2026, 5, 5)
            ),  # 3rd, eligible -> wins
        ]
    )
    await db_session.commit()

    async def is_coach(member_id):
        return member_id == coach

    result = await apply_monthly_volunteer_spotlight(
        db_session,
        now=datetime(2026, 6, 1, 0, 10, tzinfo=timezone.utc),
        excluded_member_ids={str(founder)},
        is_coach=is_coach,
    )

    assert result.member_id == winner
    assert result.monthly_hours == 3.0
    assert result.monthly_logs == 1
