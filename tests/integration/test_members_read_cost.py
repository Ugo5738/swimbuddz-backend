"""Real PostgreSQL reads preserve safety/reporting without relationship fan-out."""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event

from services.members_service.models import (
    ClubPlanVersion,
    MemberEmergencyContact,
    MemberProfile,
)
from services.members_service.routers.internal.swim_clearance import (
    ClearanceBatchRequest,
    academy_clearance,
    academy_clearance_batch,
)
from services.members_service.routers.quarterly_metrics import get_reporting_summary
from tests.factories import ClubFactory, MemberFactory, PodFactory


@contextmanager
def statements(db):
    queries = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    engine = db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield queries
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clearance_batch_is_one_narrow_query_and_retains_all_denials(db_session):
    ready, incomplete, inactive = [MemberFactory.create() for _ in range(3)]
    inactive.is_active = False
    db_session.add_all([ready, incomplete, inactive])
    await db_session.flush()
    db_session.add_all(
        [
            MemberProfile(
                member_id=ready.id,
                phone="123",
                swim_level="beginner",
                deep_water_comfort="uncomfortable",
            ),
            MemberEmergencyContact(
                member_id=ready.id,
                name="Contact",
                phone="456",
                contact_relationship="friend",
            ),
        ]
    )
    await db_session.flush()
    ids = [ready.id, incomplete.id, inactive.id, uuid4()]
    db_session.expunge_all()
    with statements(db_session) as queries:
        result = await academy_clearance_batch(
            ClearanceBatchRequest(member_ids=ids), None, db_session
        )
    assert len(queries) == 1
    assert "coach_profiles" not in queries[0] and "member_memberships" not in queries[0]
    assert "medical" not in queries[0] and "members.email" not in queries[0]
    assert result[str(ids[0])] == {"ready": True, "missing": []}
    assert result[str(ids[1])]["missing"] == [
        "contact_phone",
        "swimming_background",
        "emergency_contact",
    ]
    for member_id in ids[2:]:
        assert result[str(member_id)] == {"ready": False, "missing": ["member_account"]}
    db_session.expunge_all()
    with statements(db_session) as queries:
        single = await academy_clearance(ids[0], None, db_session)
    assert len(queries) == 1
    assert single == {"member_id": str(ids[0]), "ready": True, "missing": []}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reporting_keeps_pod_and_plan_counts_without_loading_rosters(db_session):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc)
    member = MemberFactory.create(created_at=start)
    club = ClubFactory.create(created_at=start)
    db_session.add_all([member, club])
    await db_session.flush()
    pod = PodFactory.create(club_id=club.id, pod_lead_id=member.id, created_at=start)
    plan = ClubPlanVersion(
        club_id=club.id,
        name="Quarter",
        club_fee_kobo=100,
        published_at=start,
        period_start=start.date(),
        period_end=end.date(),
        effective_from=start.date(),
    )
    db_session.add_all([pod, plan])
    await db_session.flush()
    club_id, pod_id, plan_id = club.id, pod.id, plan.id
    db_session.expunge_all()
    with statements(db_session) as queries:
        result = await get_reporting_summary(start, end, None, None, db_session)
    assert len(queries) == 8
    assert not any("club_plan_sessions" in sql for sql in queries)
    assert sum("FROM pod_assignments" in sql for sql in queries) == 1
    assert any(row["club_id"] == str(club_id) for row in result.details["clubs"])
    assert any(row["pod_id"] == str(pod_id) for row in result.details["pods"])
    assert any(row["plan_id"] == str(plan_id) for row in result.details["plans"])
