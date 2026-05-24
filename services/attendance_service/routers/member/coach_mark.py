"""Coach bulk-attendance marking endpoint (default-present model)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_members_bulk, get_session_by_id
from libs.db.session import get_async_db
from services.attendance_service.models import AttendanceRecord, AttendanceStatus
from services.attendance_service.schemas import (
    AttendanceResponse,
    CoachAttendanceMarkRequest,
    CoachAttendanceMarkResponse,
)

from ._shared import require_admin_or_coach_for_session

router = APIRouter()


@router.post(
    "/sessions/{session_id}/coach-mark",
    response_model=CoachAttendanceMarkResponse,
)
async def coach_mark_session_attendance(
    session_id: uuid.UUID,
    payload: CoachAttendanceMarkRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk attendance mark by the cohort's coach (or admin override).

    Default-present model: students NOT included in `entries` are treated
    as PRESENT — no row is written, the cohort payout calculator infers
    presence from the absence of an exception. The coach typically only
    submits entries for EXCUSED, ABSENT, or LATE statuses.

    Behavior per entry:
      - status == PRESENT: deletes any existing exception row (revert to default)
      - status in {EXCUSED, ABSENT, LATE, CANCELLED}: upserts the row by
        (session_id, member_id), overwriting prior status

    EXCUSED entries auto-create a CohortMakeupObligation downstream when
    the next coach-payout cron runs (handled by payments_service).
    """
    # Auth: admin or assigned coach
    await require_admin_or_coach_for_session(session_id, current_user, db)

    if not payload.entries:
        return CoachAttendanceMarkResponse(
            session_id=session_id, upserted=0, deleted=0, records=[]
        )

    # Resolve session. We used to reject non-cohort sessions here, but the
    # admin attendance UI needs the same upsert mechanic for community /
    # club / event sessions too — there's no other path for bulk-marking
    # paid bookings as PRESENT. The EXCUSED → CohortMakeupObligation
    # side-effect downstream is already cohort-aware and only fires when
    # the parent session has a cohort_id, so lifting the gate here is
    # safe.
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")
    # Note: we used to branch on session_data.get("cohort_id") below — see
    # PRESENT branch — but the May 2026 payout policy retired the
    # default-present model. Explicit attendance is now required for the
    # coach to get paid, so PRESENT always materialises a row regardless of
    # session kind. See payment_service/services/payout_calculator.py.

    # Pull existing rows for this session for the members in the payload.
    member_ids = [e.member_id for e in payload.entries]
    existing_rows = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.member_id.in_(member_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    existing_by_member: dict[uuid.UUID, AttendanceRecord] = {
        r.member_id: r for r in existing_rows
    }

    upserted = 0
    deleted = 0

    for entry in payload.entries:
        existing = existing_by_member.get(entry.member_id)

        # PRESENT now materialises a row for ALL session kinds. Pre-May-2026
        # cohort sessions had a "default-present" optimisation where no row =
        # present and PRESENT just removed any exception. That optimisation
        # was retired when coach payout switched to "pay only for lessons
        # actually held with the student" — under the new policy, "no row"
        # means "skip, coach not paid" (see
        # services/payments_service/services/payout_calculator.py). The
        # admin attendance UI also defaults cohort members to "absent" and
        # expects PRESENT to land an explicit row, so the legacy branch
        # silently no-op'd every cohort-present click.
        if entry.status == AttendanceStatus.PRESENT:
            if existing is None:
                db.add(
                    AttendanceRecord(
                        session_id=session_id,
                        member_id=entry.member_id,
                        status=AttendanceStatus.PRESENT,
                        notes=entry.notes,
                    )
                )
            else:
                existing.status = AttendanceStatus.PRESENT
                if entry.notes is not None:
                    existing.notes = entry.notes
            upserted += 1
            continue

        if existing is None:
            db.add(
                AttendanceRecord(
                    session_id=session_id,
                    member_id=entry.member_id,
                    status=entry.status,
                    notes=entry.notes,
                )
            )
        else:
            existing.status = entry.status
            if entry.notes is not None:
                existing.notes = entry.notes
        upserted += 1

    await db.commit()

    # Re-fetch the resulting state for the response.
    refreshed = (
        (
            await db.execute(
                select(AttendanceRecord).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.member_id.in_(member_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    members_data = await get_members_bulk(
        [str(r.member_id) for r in refreshed], calling_service="attendance"
    )
    members_map = {m["id"]: m for m in members_data}

    records = []
    for record in refreshed:
        resp = AttendanceResponse.model_validate(record)
        m = members_map.get(str(record.member_id), {})
        resp.member_name = (
            f"{m.get('first_name', '')} {m.get('last_name', '')}".strip() or None
        )
        resp.member_email = m.get("email")
        records.append(resp)

    return CoachAttendanceMarkResponse(
        session_id=session_id,
        upserted=upserted,
        deleted=deleted,
        records=records,
    )
