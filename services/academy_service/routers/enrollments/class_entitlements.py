"""Admin-only Academy class baseline and per-student allocation ledger.

Allocation reservation never marks a class paid or changes attendance. The
Sessions service must verify and reconcile the physical booking independently.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin, require_service_role
from libs.common.config import get_settings
from libs.common.service_client import get_member_attendance, internal_get
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyClassAllocation,
    AcademyClassPlan,
    Enrollment,
    EnrollmentStatus,
    Program,
)

router = APIRouter()

# Present, late and unexcused absent records consume an assigned lesson.
# Excused/cancelled records resolve attendance without consuming a lesson;
# only missing or unknown statuses need attendance reconciliation.
_RECORDED_ATTENDANCE_STATUSES = {"present", "late", "absent", "excused", "cancelled"}


class ClassBaselineInput(BaseModel):
    purchased_classes: int = Field(gt=0, le=500)
    previously_consumed: int = Field(ge=0, le=500)
    opening_as_of: datetime
    reason: str = Field(min_length=15, max_length=1000)


class ClassAllocationInput(BaseModel):
    actor_auth_id: str = Field(min_length=1, max_length=160)
    member_id: uuid.UUID
    session_id: uuid.UUID
    kind: str = Field(pattern="^(prepaid|paid_extra|makeup|support)$")
    agreed_fee_kobo: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=1000)


async def _routine_consumption(
    enrollment: Enrollment,
    plan: AcademyClassPlan,
    allocations: list[AcademyClassAllocation],
    exclude_session_id: uuid.UUID | None = None,
) -> tuple[int, list[str]]:
    """Use attendance as the source of truth for normal included cohort classes.

    Explicit unexcused absences consume a lesson; excused/cancelled records do not.
    Ambiguous missing records block new prepaid allocations rather than giving
    a learner more available credit than they have purchased.
    """
    try:
        response = await internal_get(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/cohorts/{enrollment.cohort_id}/sessions",
            calling_service="academy",
            params={"start_date": plan.opening_as_of.isoformat()},
        )
        response.raise_for_status()
        sessions = response.json()
    except Exception as exc:
        raise HTTPException(503, "Could not inspect routine Academy sessions") from exc

    allocated_ids = {str(item.session_id) for item in allocations}
    now = utc_now()
    routine = [
        item
        for item in sessions
        if item.get("session_type") == "cohort_class"
        and item.get("cohort_fee_mode", "included") == "included"
        and (item.get("cohort_attendance_mode") or "automatic") == "automatic"
        and item.get("status") in {"scheduled", "in_progress", "completed"}
        and datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00")) <= now
        and (
            enrollment.enrolled_at is None
            or datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00"))
            >= enrollment.enrolled_at
        )
        and item.get("id") not in allocated_ids
        and item.get("id") != (str(exclude_session_id) if exclude_session_id else None)
    ]
    if not routine:
        return 0, []
    ids = [item["id"] for item in routine]
    try:
        records = await get_member_attendance(
            str(enrollment.member_id),
            session_ids=ids,
            calling_service="academy",
        )
    except Exception as exc:
        raise HTTPException(503, "Could not verify routine Academy attendance") from exc
    statuses = {
        item["session_id"]: item["status"]
        for item in records
        if item.get("session_id") in ids
    }
    unverified = [
        session_id
        for session_id in ids
        if statuses.get(session_id) not in _RECORDED_ATTENDANCE_STATUSES
    ]
    return sum(
        statuses.get(session_id) in {"present", "late", "absent"} for session_id in ids
    ), unverified


async def _summary(
    db: AsyncSession,
    plan: AcademyClassPlan,
    *,
    exclude_session_id: uuid.UUID | None = None,
) -> dict:
    rows = (
        (
            await db.execute(
                select(AcademyClassAllocation).where(
                    AcademyClassAllocation.enrollment_id == plan.enrollment_id,
                    AcademyClassAllocation.status != "reversed",
                )
            )
        )
        .scalars()
        .all()
    )
    used = sum(row.kind == "prepaid" and row.status == "applied" for row in rows)
    reserved = sum(row.kind == "prepaid" and row.status == "reserved" for row in rows)
    enrollment = (
        await db.execute(select(Enrollment).where(Enrollment.id == plan.enrollment_id))
    ).scalar_one()
    if exclude_session_id is None:
        routine_used, unverified = await _routine_consumption(enrollment, plan, rows)
    else:
        routine_used, unverified = await _routine_consumption(
            enrollment, plan, rows, exclude_session_id=exclude_session_id
        )
    remaining = (
        plan.purchased_classes - plan.opening_consumed - used - reserved - routine_used
    )
    return {
        "enrollment_id": str(plan.enrollment_id),
        "purchased_classes": plan.purchased_classes,
        "opening_consumed": plan.opening_consumed,
        "applied_prepaid": used,
        "routine_attendance_consumed": routine_used,
        "unverified_routine_session_ids": unverified,
        "balance_verified": not unverified,
        "reserved_prepaid": reserved,
        "remaining": remaining,
        "baseline_as_of": plan.opening_as_of.isoformat(),
        "allocations": [
            {
                "id": str(row.id),
                "session_id": str(row.session_id),
                "kind": row.kind,
                "agreed_fee_kobo": row.agreed_fee_kobo,
                "status": row.status,
                "booking_id": str(row.booking_id) if row.booking_id else None,
            }
            for row in rows
        ],
    }


@router.get("/admin/enrollments/{enrollment_id}/class-entitlements")
async def class_entitlements(
    enrollment_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
    session_id: uuid.UUID | None = None,
):
    plan = (
        await db.execute(
            select(AcademyClassPlan).where(
                AcademyClassPlan.enrollment_id == enrollment_id
            )
        )
    ).scalar_one_or_none()
    if plan is None:
        # Paid extras can be recorded without consuming a purchased class.
        # Show earlier allocations even when there is not yet a verified plan.
        rows = (
            (
                await db.execute(
                    select(AcademyClassAllocation).where(
                        AcademyClassAllocation.enrollment_id == enrollment_id,
                        AcademyClassAllocation.status != "reversed",
                    )
                )
            )
            .scalars()
            .all()
        )
        return {
            "enrollment_id": str(enrollment_id),
            "configured": False,
            "allocations": [
                {
                    "id": str(row.id),
                    "session_id": str(row.session_id),
                    "kind": row.kind,
                    "agreed_fee_kobo": row.agreed_fee_kobo,
                    "status": row.status,
                }
                for row in rows
            ],
        }
    return {
        "configured": True,
        **(await _summary(db, plan, exclude_session_id=session_id)),
    }


@router.get("/admin/enrollments/{enrollment_id}/class-entitlements/preview")
async def preview_class_baseline(
    enrollment_id: uuid.UUID,
    session_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Suggest a historical opening balance from authoritative records.

    Suggestions are *not* approved balances. Programme duration is not a
    purchased-class contract and missing attendance must be reviewed.
    """
    enrollment = (
        await db.execute(select(Enrollment).where(Enrollment.id == enrollment_id))
    ).scalar_one_or_none()
    if enrollment is None or enrollment.cohort_id is None:
        raise HTTPException(404, "Cohort enrollment not found")
    existing = (
        await db.execute(
            select(AcademyClassPlan).where(
                AcademyClassPlan.enrollment_id == enrollment_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "Opening balance is already configured")
    program = (
        (
            await db.execute(select(Program).where(Program.id == enrollment.program_id))
        ).scalar_one_or_none()
        if enrollment.program_id
        else None
    )

    try:
        response = await internal_get(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/{session_id}",
            calling_service="academy",
        )
        response.raise_for_status()
        target = response.json()
        if target.get("session_type") != "cohort_class" or str(
            target.get("cohort_id")
        ) != str(enrollment.cohort_id):
            raise HTTPException(409, "Selected session is not in this student's cohort")
        cutoff = datetime.fromisoformat(target["starts_at"].replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise HTTPException(503, "Session timestamp lacks timezone")
        response = await internal_get(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/cohorts/{enrollment.cohort_id}/sessions",
            calling_service="academy",
            params={
                "end_date": cutoff.isoformat(),
                **(
                    {"start_date": enrollment.enrolled_at.isoformat()}
                    if enrollment.enrolled_at
                    else {}
                ),
            },
        )
        response.raise_for_status()
        sessions = response.json()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Could not retrieve Academy session history") from exc

    prior = [
        row
        for row in sessions
        if row.get("id") != str(session_id)
        and row.get("session_type") == "cohort_class"
        and row.get("status") in {"scheduled", "in_progress", "completed"}
        and row.get("cohort_fee_mode", "included") == "included"
        and (row.get("cohort_attendance_mode") or "automatic") == "automatic"
        and datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00")) < cutoff
    ]
    # Explicit allocations distinguish a paid extra from a purchased lesson,
    # even if an older session happened to have an "included" default.
    allocations = (
        (
            await db.execute(
                select(AcademyClassAllocation).where(
                    AcademyClassAllocation.enrollment_id == enrollment_id,
                    AcademyClassAllocation.status != "reversed",
                )
            )
        )
        .scalars()
        .all()
    )
    by_session = {str(row.session_id): row for row in allocations}
    ordinary_ids = [row["id"] for row in prior if row["id"] not in by_session]
    try:
        attendance = (
            (
                await get_member_attendance(
                    str(enrollment.member_id),
                    session_ids=ordinary_ids,
                    calling_service="academy",
                )
            )
            if ordinary_ids
            else []
        )
    except Exception as exc:
        raise HTTPException(503, "Could not retrieve recorded attendance") from exc
    statuses = {
        item["session_id"]: item.get("status")
        for item in attendance
        if item.get("session_id") in ordinary_ids
    }
    attended_ids = [
        session
        for session in ordinary_ids
        if statuses.get(session) in {"present", "late"}
    ]
    absent_ids = [
        session for session in ordinary_ids if statuses.get(session) == "absent"
    ]
    unresolved = [
        {"session_id": session, "status": statuses.get(session) or "missing"}
        for session in ordinary_ids
        if statuses.get(session) not in _RECORDED_ATTENDANCE_STATUSES
    ]
    prior_applied = [
        row
        for row in allocations
        if row.kind == "prepaid"
        and row.status == "applied"
        and any(
            str(row.session_id) == session["id"]
            for session in sessions
            if datetime.fromisoformat(session["starts_at"].replace("Z", "+00:00"))
            < cutoff
        )
    ]
    prior_reserved = [
        str(row.session_id)
        for row in allocations
        if row.kind == "prepaid" and row.status == "reserved"
    ]
    used = len(attended_ids) + len(absent_ids) + len(prior_applied)
    proposed_purchased = (
        int(program.duration_weeks)
        if program is not None and 0 < program.duration_weeks <= 500
        else None
    )
    return {
        "enrollment_id": str(enrollment_id),
        "suggested_purchased_classes": proposed_purchased,
        "purchased_source": "program_duration_weeks" if proposed_purchased else None,
        "suggested_previously_consumed": used,
        "suggested_opening_as_of": cutoff.isoformat(),
        "recorded_regular_attendances": len(attended_ids),
        "recorded_unexcused_absences": len(absent_ids),
        "applied_prepaid_allocations": len(prior_applied),
        "ordinary_sessions_reviewed": len(ordinary_ids),
        "unverified_sessions": unresolved,
        "pending_prepaid_session_ids": prior_reserved,
        "warning": (
            "These are suggestions, not a verified purchase entitlement. "
            "Check the original class agreement, transfers, support/make-up "
            "records and unresolved attendance before approving."
        ),
    }


@router.post("/admin/enrollments/{enrollment_id}/class-entitlements/baseline")
async def configure_class_baseline(
    enrollment_id: uuid.UUID,
    payload: ClassBaselineInput,
    actor: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """One-time verified opening balance; never reset consumed classes."""
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if enrollment is None or enrollment.cohort_id is None:
        raise HTTPException(404, "Cohort enrollment not found")
    existing = (
        await db.execute(
            select(AcademyClassPlan).where(
                AcademyClassPlan.enrollment_id == enrollment_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            409, "Class baseline already configured; do not reset history"
        )
    if payload.opening_as_of.tzinfo is None or payload.opening_as_of > utc_now():
        raise HTTPException(
            422, "Opening balance cutoff must be a past timestamp with timezone"
        )
    if payload.previously_consumed > payload.purchased_classes:
        raise HTTPException(
            422, "Previously consumed classes exceed the purchased quantity"
        )
    plan = AcademyClassPlan(
        enrollment_id=enrollment_id,
        purchased_classes=payload.purchased_classes,
        opening_consumed=payload.previously_consumed,
        opening_as_of=payload.opening_as_of,
        baseline_reason=payload.reason,
        baseline_actor_id=actor.user_id,
    )
    db.add(plan)
    await db.commit()
    return {"configured": True, **(await _summary(db, plan))}


@router.post("/admin/enrollments/{enrollment_id}/class-entitlements/allocations")
async def reserve_class_allocation(
    enrollment_id: uuid.UUID,
    payload: ClassAllocationInput,
    actor: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Reserve a learner-specific entitlement, idempotently.

    This intentionally does not create a booking or charge a member. The
    corresponding Sessions-service write is a separate step and may fail.
    """
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if enrollment is None or enrollment.cohort_id is None:
        raise HTTPException(404, "Cohort enrollment not found")
    if enrollment.member_id != payload.member_id:
        raise HTTPException(409, "Member and Academy enrollment do not match")
    if (
        enrollment.status != EnrollmentStatus.ENROLLED
        or enrollment.access_suspended
        or enrollment.paused_at
    ):
        raise HTTPException(
            409, "Enrollment is not currently entitled to Academy sessions"
        )
    plan = (
        await db.execute(
            select(AcademyClassPlan).where(
                AcademyClassPlan.enrollment_id == enrollment_id
            )
        )
    ).scalar_one_or_none()
    # An extra purchase does not consume the prepaid class ledger, so no
    # opening balance is needed. Prepaid consumption still requires one.
    if plan is None and payload.kind != "paid_extra":
        raise HTTPException(
            409, "Review and configure the historical class baseline first"
        )
    previous = (
        await db.execute(
            select(AcademyClassAllocation).where(
                AcademyClassAllocation.enrollment_id == enrollment_id,
                AcademyClassAllocation.session_id == payload.session_id,
            )
        )
    ).scalar_one_or_none()
    if previous:
        if (
            previous.kind != payload.kind
            or previous.agreed_fee_kobo != payload.agreed_fee_kobo
            or previous.status == "reversed"
        ):
            raise HTTPException(
                409, "Existing allocation differs; reviewed correction required"
            )
        return {"id": str(previous.id), "status": previous.status, "idempotent": True}
    try:
        response = await internal_get(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/{payload.session_id}",
            calling_service="academy",
        )
        response.raise_for_status()
        session = response.json()
    except Exception as exc:
        raise HTTPException(
            503, "Cannot verify the physical session. Allocation unchanged"
        ) from exc
    if (
        str(session.get("cohort_id")) != str(enrollment.cohort_id)
        or session.get("session_type") != "cohort_class"
    ):
        raise HTTPException(409, "The session does not belong to this Academy cohort")
    session_start = datetime.fromisoformat(session["starts_at"].replace("Z", "+00:00"))
    if plan is not None and session_start < plan.opening_as_of:
        raise HTTPException(
            409, "Session predates verified opening balance; review baseline"
        )
    if payload.kind == "prepaid":
        if payload.agreed_fee_kobo:
            raise HTTPException(422, "Prepaid classes cannot incur an additional fee")
        summary = await _summary(db, plan, exclude_session_id=payload.session_id)
        if not summary["balance_verified"]:
            raise HTTPException(
                409,
                "Reconcile missing routine class attendance before using more prepaid lessons",
            )
        if summary["remaining"] <= 0:
            raise HTTPException(409, "No prepaid classes remain")
    elif payload.kind == "paid_extra":
        if payload.agreed_fee_kobo <= 0:
            raise HTTPException(422, "Paid extra requires an agreed positive charge")
    else:
        if payload.agreed_fee_kobo:
            raise HTTPException(422, "Make-up and support classes cannot be charged")
        # Make-up and support approvals need their own verified source.
        raise HTTPException(409, "Use the existing approved support/make-up workflow")
    row = AcademyClassAllocation(
        enrollment_id=enrollment_id,
        session_id=payload.session_id,
        member_id=enrollment.member_id,
        kind=payload.kind,
        agreed_fee_kobo=payload.agreed_fee_kobo,
        status="reserved",
        actor_auth_id=payload.actor_auth_id,
        reason=payload.reason,
    )
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "status": "reserved", "idempotent": False}


class ClassAllocationApply(BaseModel):
    booking_id: uuid.UUID


@router.post(
    "/internal/enrollments/{enrollment_id}/class-allocations/{allocation_id}/apply"
)
async def apply_class_allocation(
    enrollment_id: uuid.UUID,
    allocation_id: uuid.UUID,
    payload: ClassAllocationApply,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Sessions service confirms a durable booking was written.

    The allocation is reserved until this call. A retry with the same booking
    is safe; an alternate booking ID is a reconciliation conflict.
    """
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update()
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(404, "Enrollment not found")
    allocation = (
        await db.execute(
            select(AcademyClassAllocation).where(
                AcademyClassAllocation.id == allocation_id,
                AcademyClassAllocation.enrollment_id == enrollment_id,
            )
        )
    ).scalar_one_or_none()
    if allocation is None:
        raise HTTPException(404, "Class allocation not found")
    if allocation.status == "reversed":
        raise HTTPException(409, "A reversed allocation cannot be applied")
    if allocation.booking_id and allocation.booking_id != payload.booking_id:
        raise HTTPException(409, "Allocation already linked to a different booking")
    allocation.booking_id = payload.booking_id
    allocation.status = "applied"
    if allocation.applied_at is None:
        allocation.applied_at = utc_now()
    await db.commit()
    return {
        "id": str(allocation.id),
        "status": allocation.status,
        "booking_id": str(allocation.booking_id),
    }
