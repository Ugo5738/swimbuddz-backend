"""Admin-only Academy class baseline and per-student allocation ledger.

Allocation reservation never marks a class paid or changes attendance. The
Sessions service must verify and reconcile the physical booking independently.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin, require_service_role
from libs.common.config import get_settings
from libs.common.service_client import internal_get
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.academy_service.models import (
    AcademyClassAllocation,
    AcademyClassPlan,
    Enrollment,
    EnrollmentStatus,
)

router = APIRouter()


class ClassBaselineInput(BaseModel):
    purchased_classes: int = Field(gt=0, le=500)
    previously_consumed: int = Field(ge=0, le=500)
    reason: str = Field(min_length=15, max_length=1000)


class ClassAllocationInput(BaseModel):
    member_id: uuid.UUID
    session_id: uuid.UUID
    kind: str = Field(pattern="^(prepaid|paid_extra|makeup|support)$")
    agreed_fee_kobo: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=1000)


async def _summary(db: AsyncSession, plan: AcademyClassPlan) -> dict:
    rows = (
        await db.execute(
            select(AcademyClassAllocation).where(
                AcademyClassAllocation.enrollment_id == plan.enrollment_id,
                AcademyClassAllocation.status != "reversed",
            )
        )
    ).scalars().all()
    used = sum(row.kind == "prepaid" and row.status == "applied" for row in rows)
    reserved = sum(row.kind == "prepaid" and row.status == "reserved" for row in rows)
    remaining = plan.purchased_classes - plan.opening_consumed - used - reserved
    return {
        "enrollment_id": str(plan.enrollment_id),
        "purchased_classes": plan.purchased_classes,
        "opening_consumed": plan.opening_consumed,
        "applied_prepaid": used,
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
):
    plan = (
        await db.execute(
            select(AcademyClassPlan).where(AcademyClassPlan.enrollment_id == enrollment_id)
        )
    ).scalar_one_or_none()
    if plan is None:
        return {"enrollment_id": str(enrollment_id), "configured": False}
    return {"configured": True, **(await _summary(db, plan))}


@router.post("/admin/enrollments/{enrollment_id}/class-entitlements/baseline")
async def configure_class_baseline(
    enrollment_id: uuid.UUID,
    payload: ClassBaselineInput,
    actor: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """One-time verified opening balance; never reset consumed classes."""
    enrollment = (
        await db.execute(select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update())
    ).scalar_one_or_none()
    if enrollment is None or enrollment.cohort_id is None:
        raise HTTPException(404, "Cohort enrollment not found")
    existing = (
        await db.execute(
            select(AcademyClassPlan).where(AcademyClassPlan.enrollment_id == enrollment_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, "Class baseline already configured; do not reset history")
    if payload.previously_consumed > payload.purchased_classes:
        raise HTTPException(422, "Previously consumed classes exceed the purchased quantity")
    plan = AcademyClassPlan(
        enrollment_id=enrollment_id,
        purchased_classes=payload.purchased_classes,
        opening_consumed=payload.previously_consumed,
        opening_as_of=utc_now(),
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
        await db.execute(select(Enrollment).where(Enrollment.id == enrollment_id).with_for_update())
    ).scalar_one_or_none()
    if enrollment is None or enrollment.cohort_id is None:
        raise HTTPException(404, "Cohort enrollment not found")
    if enrollment.member_id != payload.member_id:
        raise HTTPException(409, "Member and Academy enrollment do not match")
    if enrollment.status != EnrollmentStatus.ENROLLED or enrollment.access_suspended or enrollment.paused_at:
        raise HTTPException(409, "Enrollment is not currently entitled to Academy sessions")
    plan = (
        await db.execute(select(AcademyClassPlan).where(AcademyClassPlan.enrollment_id == enrollment_id))
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(409, "Review and configure the historical class baseline first")
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
            raise HTTPException(409, "Existing allocation differs; reviewed correction required")
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
        raise HTTPException(503, "Cannot verify the physical session. Allocation unchanged") from exc
    if (
        str(session.get("cohort_id")) != str(enrollment.cohort_id)
        or session.get("session_type") != "cohort_class"
    ):
        raise HTTPException(409, "The session does not belong to this Academy cohort")
    if payload.kind == "prepaid":
        if payload.agreed_fee_kobo:
            raise HTTPException(422, "Prepaid classes cannot incur an additional fee")
        summary = await _summary(db, plan)
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
        actor_auth_id=actor.user_id,
        reason=payload.reason,
    )
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "status": "reserved", "idempotent": False}


class ClassAllocationApply(BaseModel):
    booking_id: uuid.UUID


@router.post("/internal/enrollments/{enrollment_id}/class-allocations/{allocation_id}/apply")
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
