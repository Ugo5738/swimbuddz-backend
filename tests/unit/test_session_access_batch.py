import uuid
from unittest.mock import AsyncMock

import pytest

from services.sessions_service.services import session_access
from tests.factories import SessionFactory


@pytest.mark.asyncio
async def test_session_access_context_batches_unique_cohorts_and_pods(monkeypatch):
    cohort_id = uuid.uuid4()
    other_cohort_id = uuid.uuid4()
    pod_id = uuid.uuid4()
    member_id = uuid.uuid4()

    cohort_session = SessionFactory.create(cohort_id=cohort_id)
    same_cohort_session = SessionFactory.create(cohort_id=cohort_id)
    confirmed_cohort_session = SessionFactory.create(cohort_id=other_cohort_id)
    pod_session = SessionFactory.create(pod_id=pod_id)
    same_pod_session = SessionFactory.create(pod_id=pod_id)

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
    monkeypatch.setattr(
        session_access,
        "check_cohort_enrollments_batch",
        cohort_batch,
    )
    monkeypatch.setattr(session_access, "get_pod_rosters_batch", pod_batch)

    cohort_access, pod_rosters = await session_access.get_sessions_access_context(
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
    assert cohort_access[str(cohort_id)]["enrolled"] is True
    assert pod_rosters[str(pod_id)] == [str(member_id)]
