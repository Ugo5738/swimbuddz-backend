from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from services.members_service.routers import experience_admin as admin
from services.members_service.schemas.experience import ExperienceEventsUpdate
from services.events_service.routers import experience_links as events


def operation():
    return NS(
        id=uuid4(),
        offering_id=uuid4(),
        old_event_ids=[str(uuid4())],
        status="pending",
        error=None,
        request={"events": [{"replaced_session_ids": [str(uuid4())]}]},
    )


@pytest.mark.asyncio
async def test_compensation_restores_old_binding_and_tombstones_uncertain_session_write(
    monkeypatch,
):
    op = operation()
    db = NS(commit=AsyncMock())
    bind = AsyncMock()
    undo = AsyncMock()
    monkeypatch.setattr(admin, "event_request", bind)
    monkeypatch.setattr(admin, "schedule_change_request", undo)
    await admin.compensate_configuration(db, op)
    undo.assert_awaited_once_with(f"operations/{op.id}/undo", {})
    bind.assert_awaited_once_with(
        "bind",
        {
            "offering_id": str(op.offering_id),
            "event_ids": op.old_event_ids,
            "operation_id": str(op.id),
            "compensate": True,
        },
    )
    assert op.status == "failed" and "restored" in op.error


@pytest.mark.asyncio
async def test_failed_compensation_is_visible_and_recovery_can_be_retried(monkeypatch):
    op = operation()
    db = NS(commit=AsyncMock())
    bind = AsyncMock(side_effect=[HTTPException(503, "Events unavailable"), []])
    monkeypatch.setattr(admin, "event_request", bind)
    monkeypatch.setattr(admin, "schedule_change_request", AsyncMock())
    await admin.compensate_configuration(db, op)
    assert (
        op.status == "needs_reconciliation" and "Event binding restoration" in op.error
    )
    await admin.compensate_configuration(db, op)
    assert op.status == "failed" and bind.await_count == 2


@pytest.mark.asyncio
async def test_replacement_failure_after_event_binding_restores_previous_configuration(
    monkeypatch,
):
    old, new, swim = uuid4(), uuid4(), uuid4()
    offering = NS(
        id=uuid4(),
        name="Q4 trip",
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        event_links=[NS(event_id=old)],
    )
    op_box = []

    async def flush():
        for item in op_box:
            if item.id is None:
                item.id = uuid4()

    empty = NS(scalars=lambda: NS(all=lambda: []))
    db = NS(
        add=lambda item: op_box.append(item),
        flush=flush,
        commit=AsyncMock(),
        rollback=AsyncMock(),
        get=AsyncMock(side_effect=lambda *args, **kwargs: op_box[0]),
        execute=AsyncMock(return_value=empty),
    )
    monkeypatch.setattr(admin, "lock_offering", AsyncMock(return_value=offering))
    monkeypatch.setattr(admin, "assert_unsold", AsyncMock())
    event_row = {
        "id": str(new),
        "start_time": "2026-12-05T09:00:00+01:00",
        "end_time": "2026-12-05T17:00:00+01:00",
    }
    bind = AsyncMock(side_effect=[[event_row], [], []])
    monkeypatch.setattr(admin, "event_request", bind)
    monkeypatch.setattr(
        admin,
        "fetch_schedule",
        AsyncMock(
            return_value=[{"id": str(swim), "starts_at": "2026-12-05T09:00:00+01:00"}]
        ),
    )
    calls = AsyncMock(
        side_effect=[
            {"valid": True},
            HTTPException(409, "Booking arrived after validation"),
            {"status": "reverted"},
        ]
    )
    monkeypatch.setattr(admin, "schedule_change_request", calls)
    body = ExperienceEventsUpdate(
        events=[
            {"event_id": new, "club_impact": "replaces", "replaced_session_ids": [swim]}
        ]
    )
    with pytest.raises(HTTPException, match="did not complete"):
        await admin.set_events(offering.id, body, db)
    assert offering.event_links[0].event_id == old
    assert op_box[0].status == "failed"
    assert bind.call_args_list[1].args[1]["event_ids"] == [str(new)]
    assert bind.call_args_list[2].args[1]["event_ids"] == [str(old)]
    assert bind.call_args_list[2].args[1]["compensate"] is True


@pytest.mark.asyncio
async def test_late_binding_request_cannot_undo_completed_recovery():
    id, offering = uuid4(), uuid4()
    db = NS(
        execute=AsyncMock(),
        get=AsyncMock(return_value=NS(offering_id=offering, status="reverted")),
    )
    with pytest.raises(HTTPException, match="compensated"):
        await events.bind_events(
            events.BindEvents(
                operation_id=id, offering_id=offering, event_ids=[uuid4()]
            ),
            db,
        )
    assert db.execute.await_count == 1  # advisory lock only; no Event mutation


@pytest.mark.parametrize("status,compensate", [("applied", False), ("reverted", True)])
@pytest.mark.asyncio
async def test_completed_binding_or_compensation_replay_cannot_overwrite_newer_configuration(
    monkeypatch, status, compensate
):
    id, offering, event = uuid4(), uuid4(), uuid4()
    db = NS(
        execute=AsyncMock(),
        get=AsyncMock(
            return_value=NS(offering_id=offering, status=status, event_ids=[str(event)])
        ),
    )
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(events, "query_events", query)
    await events.bind_events(
        events.BindEvents(
            operation_id=id,
            offering_id=offering,
            event_ids=[event],
            compensate=compensate,
        ),
        db,
    )
    query.assert_awaited_once()
    assert db.execute.await_count == 1  # replay only reads; no rebind transaction
