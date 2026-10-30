"""Persisted defaults support all consumers while protecting saved exceptions."""

import uuid
from unittest.mock import patch
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from services.attendance_service.services.academy_defaults import reconcile_session

NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def session():
    return {
        "id": str(uuid.uuid4()),
        "session_type": "cohort_class",
        "status": "scheduled",
        "starts_at": "2026-10-18T15:00:00+00:00",
        "cohort_fee_mode": "included",
    }


def student(mid):
    return {
        "member_id": str(mid),
        "status": "enrolled",
        "created_at": "2026-10-01T00:00:00+00:00",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_preserves_every_coach_exception(db_session):
    swim = session()
    members = [uuid.uuid4() for _ in range(6)]
    statuses = [
        AttendanceStatus.ABSENT,
        AttendanceStatus.EXCUSED,
        AttendanceStatus.LATE,
        AttendanceStatus.CANCELLED,
        AttendanceStatus.PRESENT,
    ]
    db_session.add_all(
        [
            AttendanceRecord(
                session_id=uuid.UUID(swim["id"]),
                member_id=mid,
                status=status,
                notes="Coach decision",
            )
            for mid, status in zip(members, statuses)
        ]
    )
    await db_session.flush()
    kwargs = {"cleared_member_ids": {str(mid) for mid in members}, "now": NOW}
    first = await reconcile_session(
        db_session, swim, [student(mid) for mid in members], **kwargs
    )
    second = await reconcile_session(
        db_session, swim, [student(mid) for mid in members], **kwargs
    )
    assert first["created"] == 1 and second["created"] == 0
    rows = list(
        (
            await db_session.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == uuid.UUID(swim["id"])
                )
            )
        ).all()
    )
    by_member = {r.member_id: r for r in rows}
    assert len(rows) == 6 and by_member[members[-1]].status == AttendanceStatus.PRESENT
    for mid, status in zip(members, statuses):
        assert (
            by_member[mid].status == status and by_member[mid].notes == "Coach decision"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dry_run_and_uncleared_student_never_write(db_session):
    swim, mid = session(), uuid.uuid4()
    assert (
        await reconcile_session(
            db_session, swim, [student(mid)], cleared_member_ids=set(), now=NOW
        )
    )["created"] == 0
    assert (
        await reconcile_session(
            db_session,
            swim,
            [student(mid)],
            cleared_member_ids={str(mid)},
            now=NOW,
            dry_run=True,
        )
    )["created"] == 1
    assert (
        list(
            (
                await db_session.scalars(
                    select(AttendanceRecord).where(
                        AttendanceRecord.session_id == uuid.UUID(swim["id"])
                    )
                )
            ).all()
        )
        == []
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exception_created_after_read_wins_the_insert_race(db_session):
    swim, mid = session(), uuid.uuid4()
    # Force an initially-empty read, then let the real INSERT meet an exception
    # written in the meantime. The unique constraint must preserve that decision.
    real_scalars = db_session.scalars
    calls = 0

    async def race(statement):
        nonlocal calls
        calls += 1
        if calls == 1:
            empty = await real_scalars(statement)
            empty.all()  # Consume before creating the concurrent exception.
            db_session.add(
                AttendanceRecord(
                    session_id=uuid.UUID(swim["id"]),
                    member_id=mid,
                    status=AttendanceStatus.EXCUSED,
                    notes="Coach exception",
                )
            )
            await db_session.flush()
            return type("Empty", (), {"all": lambda self: []})()
        return await real_scalars(statement)

    with patch.object(db_session, "scalars", side_effect=race):
        assert (
            await reconcile_session(
                db_session, swim, [student(mid)], cleared_member_ids={str(mid)}, now=NOW
            )
        )["created"] == 0
    row = (
        await real_scalars(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == uuid.UUID(swim["id"])
            )
        )
    ).one()
    assert row.status == AttendanceStatus.EXCUSED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_discovers_subsequent_cohorts_without_config_changes(
    db_session, monkeypatch
):
    import httpx
    from contextlib import asynccontextmanager
    from services.attendance_service.services import academy_defaults as service

    first, subsequent = str(uuid.uuid4()), str(uuid.uuid4())
    mid = uuid.uuid4()
    swims = {first: session(), subsequent: session()}
    current = [first]

    @asynccontextmanager
    async def database():
        yield db_session

    async def get(*, path, **kwargs):
        if path == "/internal/academy/cohorts":
            value = {"cohorts": [{"id": cid} for cid in current]}
        elif path.endswith("enrolled-students"):
            value = [student(mid)]
        else:
            cid = path.split("/")[-2]
            value = [swims[cid]]
        return httpx.Response(
            200, json=value, request=httpx.Request("GET", "http://service.test")
        )

    async def post(**kwargs):
        return httpx.Response(
            200,
            json={str(mid): {"ready": True}},
            request=httpx.Request("POST", "http://service.test"),
        )

    monkeypatch.setattr(service, "AsyncSessionLocal", database)
    monkeypatch.setattr(service, "internal_get", get)
    monkeypatch.setattr(service, "internal_post", post)
    monkeypatch.setattr(service, "utc_now", lambda: NOW)
    assert (await service.reconcile_academy_defaults())["created"] == 1
    current.append(subsequent)
    result = await service.reconcile_academy_defaults()
    assert result["created"] == 1 and not result["errors"]
    assert result["sessions"][0]["session_id"] == swims[subsequent]["id"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("fee_mode", ["included", "paid_extra"])
async def test_individual_extra_never_defaults_whole_cohort_present(
    db_session, fee_mode
):
    swim = {
        **session(),
        "cohort_attendance_mode": "individual",
        "cohort_fee_mode": fee_mode,
    }
    members = [uuid.uuid4() for _ in range(3)]
    db_session.add(
        AttendanceRecord(
            session_id=uuid.UUID(swim["id"]),
            member_id=members[0],
            status=AttendanceStatus.PRESENT,
            notes="Explicit participant",
        )
    )
    await db_session.flush()
    result = await reconcile_session(
        db_session,
        swim,
        [student(mid) for mid in members],
        cleared_member_ids={str(mid) for mid in members},
        now=NOW,
    )
    assert result["created"] == 0
    rows = list(
        (
            await db_session.scalars(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == uuid.UUID(swim["id"])
                )
            )
        ).all()
    )
    assert len(rows) == 1 and rows[0].member_id == members[0]
    assert rows[0].notes == "Explicit participant"
