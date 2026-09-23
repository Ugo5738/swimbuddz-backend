import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.sessions_service.services import session_access
from tests.factories import SessionFactory


@pytest.mark.asyncio
async def test_session_access_context_batches_unique_cohorts_and_pods(monkeypatch):
    cohort_id = uuid.uuid4()
    other_cohort_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    club_id = uuid.uuid4()
    member_id = uuid.uuid4()

    cohort_session = SessionFactory.create(cohort_id=cohort_id)
    same_cohort_session = SessionFactory.create(cohort_id=cohort_id)
    confirmed_cohort_session = SessionFactory.create(cohort_id=other_cohort_id)
    pod_session = SessionFactory.create(club_id=club_id, pod_id=pod_id)
    same_pod_session = SessionFactory.create(club_id=club_id, pod_id=pod_id)

    cohort_batch = AsyncMock(
        return_value={
            str(cohort_id): {
                "enrolled": True,
                "status": "enrolled",
                "access_suspended": False,
            }
        }
    )
    pod_batch = AsyncMock(return_value={str(pod_id): [str(member_id)]})
    club_batch = AsyncMock(return_value={str(pod_session.id): {"allowed": True}})
    event_batch = AsyncMock(return_value={})
    monkeypatch.setattr(
        session_access,
        "check_cohort_enrollments_batch",
        cohort_batch,
    )
    monkeypatch.setattr(session_access, "get_pod_rosters_batch", pod_batch)
    monkeypatch.setattr(session_access, "check_club_access_batch", club_batch)
    monkeypatch.setattr(session_access, "check_event_attendance_batch", event_batch)

    (
        cohort_access,
        pod_rosters,
        club_access,
        event_access,
    ) = await session_access.get_sessions_access_context(
        sessions=[
            cohort_session,
            same_cohort_session,
            confirmed_cohort_session,
            pod_session,
            same_pod_session,
        ],
        member_payload={"member_id": str(member_id)},
        confirmed_session_ids={confirmed_cohort_session.id},
    )

    cohort_batch.assert_awaited_once_with(
        [str(cohort_id)],
        str(member_id),
        calling_service="sessions",
    )
    pod_batch.assert_awaited_once_with(
        [str(pod_id)],
        calling_service="sessions",
    )
    club_batch.assert_awaited_once()
    club_checks = club_batch.await_args.args[0]
    assert {item["context_key"] for item in club_checks} == {
        str(pod_session.id),
        str(same_pod_session.id),
    }
    assert {item["club_id"] for item in club_checks} == {str(club_id)}
    assert cohort_access[str(cohort_id)]["enrolled"] is True
    assert pod_rosters[str(pod_id)] == [str(member_id)]
    assert club_access[str(pod_session.id)]["allowed"] is True
    assert event_access == {}


@pytest.mark.asyncio
async def test_event_access_batches_entitlements_at_each_session_date(monkeypatch):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    club_expires = now + timedelta(days=15)
    event_id = uuid.uuid4()
    member_id = uuid.uuid4()
    eligible_session = SessionFactory.create(
        event_id=event_id,
        starts_at=now + timedelta(days=10),
        ends_at=now + timedelta(days=10, hours=2),
    )
    expired_session = SessionFactory.create(
        event_id=event_id,
        starts_at=now + timedelta(days=20),
        ends_at=now + timedelta(days=20, hours=2),
    )
    event_batch = AsyncMock(return_value={})
    monkeypatch.setattr(
        session_access,
        "check_cohort_enrollments_batch",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        session_access,
        "get_pod_rosters_batch",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        session_access,
        "check_club_access_batch",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(session_access, "check_event_attendance_batch", event_batch)

    await session_access.get_sessions_access_context(
        sessions=[eligible_session, expired_session],
        member_payload={
            "member_id": str(member_id),
            "club_paid_until": club_expires.isoformat(),
        },
        confirmed_session_ids=set(),
        now=now,
    )

    checks = event_batch.await_args.kwargs["checks"]
    by_context = {item["context_key"]: item for item in checks}
    assert by_context[str(eligible_session.id)]["paid_tiers"] == ["club"]
    assert by_context[str(expired_session.id)]["paid_tiers"] == []
