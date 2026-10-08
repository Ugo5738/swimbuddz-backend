"""Academy cohort corrections with durable journey identity and financial gates."""

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import internal_get
from libs.db.session import get_async_db
from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    AcademyJourney,
    AcademyEnrollmentChange,
    Cohort,
    Program,
    Enrollment,
    EnrollmentStatus,
    CohortStatus,
    PaymentStatus,
    InstallmentStatus,
)
from services.academy_service.routers._shared import (
    _resolve_enrollment_total_fee,
    _resolve_enrollment_membership_policy,
    _sync_installment_state_for_enrollment,
)

router = APIRouter()


class ChangeCohortRequest(BaseModel):
    target_cohort_id: uuid.UUID


class ChangeCohortResult(BaseModel):
    state: str
    enrollment_id: uuid.UUID | None = None
    change_id: uuid.UUID
    message: str


async def financial_state(enrollment_id: uuid.UUID) -> dict:
    """Failure to inspect payments must never permit an automatic switch."""
    try:
        response = await internal_get(
            service_url=get_settings().PAYMENTS_SERVICE_URL,
            path=f"/internal/payments/academy/enrollments/{enrollment_id}/financial-state",
            calling_service="academy",
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Payment reconciliation is temporarily unavailable. No enrollment was changed.",
        ) from exc


@router.get("/admin/academy/enrollment-changes")
async def list_academy_change_reviews(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin review queue. Financial settlement is not approved by this endpoint."""
    changes = (
        (
            await db.execute(
                select(AcademyEnrollmentChange)
                .where(AcademyEnrollmentChange.state == "needs_review")
                .order_by(AcademyEnrollmentChange.created_at)
            )
        )
        .scalars()
        .all()
    )
    enrollment_ids = {item.from_enrollment_id for item in changes}
    target_ids = {item.target_cohort_id for item in changes}
    enrollments = (
        (await db.execute(select(Enrollment).where(Enrollment.id.in_(enrollment_ids))))
        .scalars()
        .all()
        if enrollment_ids
        else []
    )
    cohorts = (
        (await db.execute(select(Cohort).where(Cohort.id.in_(target_ids))))
        .scalars()
        .all()
        if target_ids
        else []
    )
    source_cohort_ids = {item.cohort_id for item in enrollments if item.cohort_id}
    source_cohorts = (
        (await db.execute(select(Cohort).where(Cohort.id.in_(source_cohort_ids))))
        .scalars()
        .all()
        if source_cohort_ids
        else []
    )
    enrollment_by_id = {item.id: item for item in enrollments}
    cohort_by_id = {item.id: item for item in cohorts + source_cohorts}
    return [
        {
            "id": str(change.id),
            "journey_id": str(change.journey_id),
            "from_enrollment_id": str(change.from_enrollment_id),
            "target_cohort_id": str(change.target_cohort_id),
            "member_id": str(enrollment_by_id[change.from_enrollment_id].member_id)
            if change.from_enrollment_id in enrollment_by_id
            else None,
            "original_cohort_name": cohort_by_id[
                enrollment_by_id[change.from_enrollment_id].cohort_id
            ].name
            if change.from_enrollment_id in enrollment_by_id
            and enrollment_by_id[change.from_enrollment_id].cohort_id in cohort_by_id
            else None,
            "target_cohort_name": cohort_by_id[change.target_cohort_id].name
            if change.target_cohort_id in cohort_by_id
            else None,
            "state": change.state,
            "snapshot": change.snapshot,
            "created_at": change.created_at.isoformat(),
        }
        for change in changes
    ]


@router.post("/admin/academy/enrollment-changes/{change_id}/reject")
async def reject_enrollment_change(
    change_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Reject a request without altering the source enrollment or its money."""
    change = (
        await db.execute(
            select(AcademyEnrollmentChange)
            .where(AcademyEnrollmentChange.id == change_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not change:
        raise HTTPException(status_code=404, detail="Change request not found")
    if change.state == "rejected":
        return {"state": "rejected", "change_id": str(change.id)}
    if change.state != "needs_review":
        raise HTTPException(status_code=409, detail="This request is no longer pending")
    change.state = "rejected"
    change.snapshot = {
        **(change.snapshot or {}),
        "reviewed_by_auth_id": current_user.user_id,
        "reviewed_at": utc_now().isoformat(),
    }
    await db.commit()
    return {"state": "rejected", "change_id": str(change.id)}


class ApproveUnpaidCohortChange(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/admin/academy/enrollment-changes/{change_id}/approve-unpaid")
async def approve_unpaid_enrollment_change(
    change_id: uuid.UUID,
    payload: ApproveUnpaidCohortChange,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Finalize a correction ONLY if no payment or attended progress can be lost.

    Payments-service must certify that every previous attempt is documented
    closed-unpaid. Verified or proof-pending receipts are never moved by this
    endpoint; those need the separate finance allocation/credit process.
    """
    change = (
        await db.execute(
            select(AcademyEnrollmentChange)
            .where(AcademyEnrollmentChange.id == change_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Change request not found")
    if change.state == "completed" and change.to_enrollment_id:
        return {"state": "completed", "enrollment_id": str(change.to_enrollment_id)}
    if change.state != "needs_review":
        raise HTTPException(409, "This request is no longer pending")

    enrollment = (
        await db.execute(
            select(Enrollment)
            .where(Enrollment.id == change.from_enrollment_id)
            .options(
                selectinload(Enrollment.installments),
                selectinload(Enrollment.progress_records),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not enrollment or enrollment.status not in {
        EnrollmentStatus.PENDING_APPROVAL,
        EnrollmentStatus.WAITLIST,
    }:
        raise HTTPException(409, "Original enrollment is no longer eligible")
    if (
        enrollment.payment_status != PaymentStatus.PENDING
        or enrollment.paid_at is not None
        or enrollment.progress_records
        or any(
            item.status in {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
            or item.payment_reference
            for item in enrollment.installments
        )
    ):
        raise HTTPException(
            409,
            "Enrollment has recorded settlement or progress requiring financial review",
        )
    financial = await financial_state(enrollment.id)
    local_references = {
        reference
        for reference in [
            enrollment.payment_reference,
            *(item.payment_reference for item in enrollment.installments),
        ]
        if reference
    }
    if not local_references.issubset(set(financial.get("references") or [])):
        raise HTTPException(
            409,
            "A recorded payment reference cannot be verified by Payments; reconcile it before approving",
        )
    if not financial.get("all_unpaid_closed", False):
        raise HTTPException(
            409,
            "Close and verify all unpaid checkout attempts in Payments, or reconcile received funds before approval",
        )
    target = (
        await db.execute(
            select(Cohort).where(Cohort.id == change.target_cohort_id).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        not target
        or target.program_id != enrollment.program_id
        or target.status
        not in {
            CohortStatus.OPEN,
            CohortStatus.ACTIVE,
        }
    ):
        raise HTTPException(409, "Destination cohort is unavailable")
    now = utc_now()
    if target.status == CohortStatus.ACTIVE:
        week = max(1, ((now - target.start_date).days // 7) + 1)
        if not target.allow_mid_entry or week > target.mid_entry_cutoff_week:
            raise HTTPException(409, "Mid-entry cutoff has passed")
    program = (
        await db.execute(select(Program).where(Program.id == enrollment.program_id))
    ).scalar_one_or_none()
    if not program or not program.is_published:
        raise HTTPException(409, "Academy programme is not published")
    existing = (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.member_id == enrollment.member_id,
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [
                        EnrollmentStatus.PENDING_APPROVAL,
                        EnrollmentStatus.ENROLLED,
                        EnrollmentStatus.WAITLIST,
                    ]
                ),
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "Member already has an active destination placement")
    count = (
        await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [EnrollmentStatus.PENDING_APPROVAL, EnrollmentStatus.ENROLLED]
                ),
            )
        )
    ).scalar_one()
    if target.capacity is not None and count >= target.capacity:
        raise HTTPException(409, "Destination cohort is full")

    enrollment.status = EnrollmentStatus.DROPPED
    enrollment.dropped_at = now
    replacement = Enrollment(
        member_id=enrollment.member_id,
        member_auth_id=enrollment.member_auth_id,
        program_id=enrollment.program_id,
        cohort_id=target.id,
        preferences=dict(enrollment.preferences or {}),
        status=EnrollmentStatus.PENDING_APPROVAL,
        payment_status=PaymentStatus.PENDING,
        price_snapshot_amount=_resolve_enrollment_total_fee(program, target),
        currency_snapshot=program.currency or "NGN",
        membership_policy_snapshot=_resolve_enrollment_membership_policy(
            program, target
        ),
        uses_installments=False,
    )
    db.add(replacement)
    await db.flush()
    await _sync_installment_state_for_enrollment(db, replacement)
    change.state = "completed"
    change.to_enrollment_id = replacement.id
    change.snapshot = {
        **(change.snapshot or {}),
        "resolved_by_auth_id": current_user.user_id,
        "resolved_at": now.isoformat(),
        "resolution_reason": payload.reason,
        "financial_state_at_approval": financial,
        "financial_resolution": "all_unpaid_closed",
    }
    await db.commit()
    return {"state": "completed", "enrollment_id": str(replacement.id)}


@router.get("/my-enrollment-change-requests")
async def my_enrollment_change_requests(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Member-visible request status, scoped by the original enrollment owner."""
    result = await db.execute(
        select(AcademyEnrollmentChange, Enrollment)
        .join(Enrollment, AcademyEnrollmentChange.from_enrollment_id == Enrollment.id)
        .where(Enrollment.member_auth_id == current_user.user_id)
        .order_by(AcademyEnrollmentChange.created_at.desc())
    )
    return [
        {
            "id": str(change.id),
            "from_enrollment_id": str(change.from_enrollment_id),
            "target_cohort_id": str(change.target_cohort_id),
            "state": change.state,
            "created_at": change.created_at.isoformat(),
        }
        for change, _ in result.all()
    ]


@router.get("/my-academy-journeys")
async def my_academy_journeys(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Group a member's cohort attempts by programme without collapsing history."""
    rows = (
        (
            await db.execute(
                select(Enrollment)
                .where(Enrollment.member_auth_id == current_user.user_id)
                .options(
                    selectinload(Enrollment.cohort), selectinload(Enrollment.program)
                )
                .order_by(Enrollment.created_at)
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, dict] = {}
    for e in rows:
        key = str(e.program_id)
        item = grouped.setdefault(
            key,
            {
                "program_id": key,
                "program_name": e.program.name if e.program else "Academy programme",
                "enrollments": [],
            },
        )
        item["enrollments"].append(
            {
                "id": str(e.id),
                "cohort_id": str(e.cohort_id) if e.cohort_id else None,
                "cohort_name": e.cohort.name if e.cohort else None,
                "status": e.status.value,
                "payment_status": e.payment_status.value,
            }
        )
    return list(grouped.values())


@router.post(
    "/my-enrollments/{enrollment_id}/change-cohort", response_model=ChangeCohortResult
)
async def change_my_cohort(
    enrollment_id: uuid.UUID,
    payload: ChangeCohortRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    # Lock the original and destination cohort to prevent simultaneous switches
    # from claiming the same last seat.
    enrollment = (
        await db.execute(
            select(Enrollment)
            .where(
                Enrollment.id == enrollment_id,
                Enrollment.member_auth_id == current_user.user_id,
            )
            .options(
                selectinload(Enrollment.installments),
                selectinload(Enrollment.progress_records),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not enrollment:
        raise HTTPException(404, "Enrollment not found")
    if not enrollment.cohort_id or not enrollment.program_id:
        raise HTTPException(409, "Only cohort-based enrollments may be changed")
    if enrollment.cohort_id == payload.target_cohort_id:
        raise HTTPException(409, "Already enrolled in the selected cohort")
    if enrollment.status not in {
        EnrollmentStatus.PENDING_APPROVAL,
        EnrollmentStatus.WAITLIST,
    }:
        raise HTTPException(
            409, "Active or completed cohort changes require admin review"
        )
    target = (
        await db.execute(
            select(Cohort)
            .where(Cohort.id == payload.target_cohort_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not target or target.program_id != enrollment.program_id:
        raise HTTPException(400, "Select a cohort from the same Academy programme")
    now = utc_now()
    if target.status not in {CohortStatus.OPEN, CohortStatus.ACTIVE}:
        raise HTTPException(409, "The selected cohort is not accepting students")
    if target.status == CohortStatus.ACTIVE:
        week = max(1, ((now - target.start_date).days // 7) + 1)
        if not target.allow_mid_entry or week > target.mid_entry_cutoff_week:
            raise HTTPException(409, "This cohort's mid-entry window has closed")
    program = (
        await db.execute(select(Program).where(Program.id == target.program_id))
    ).scalar_one()
    if not program.is_published:
        raise HTTPException(409, "Programme is not published")
    journey = (
        await db.execute(
            select(AcademyJourney)
            .where(
                AcademyJourney.member_id == enrollment.member_id,
                AcademyJourney.program_id == enrollment.program_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not journey:
        journey = AcademyJourney(
            member_id=enrollment.member_id, program_id=enrollment.program_id
        )
        db.add(journey)
        await db.flush()
    existing_request = (
        (
            await db.execute(
                select(AcademyEnrollmentChange)
                .where(
                    AcademyEnrollmentChange.from_enrollment_id == enrollment.id,
                    AcademyEnrollmentChange.state == "needs_review",
                )
                .order_by(AcademyEnrollmentChange.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if existing_request:
        if existing_request.target_cohort_id != target.id:
            raise HTTPException(409, "A cohort change is already awaiting review")
        return ChangeCohortResult(
            state="needs_review",
            change_id=existing_request.id,
            message="Your request is already awaiting review. Your existing enrollment remains unchanged.",
        )
    # Inspect BOTH service-owned payment rows and academy-side settlement
    # artifacts. An initiated bank transfer is not necessarily unpaid.
    financial = await financial_state(enrollment.id)
    local_money = (
        enrollment.payment_status != PaymentStatus.PENDING
        or enrollment.paid_at is not None
        or bool(enrollment.payment_reference)
        or any(
            i.status != InstallmentStatus.PENDING or i.payment_reference
            for i in enrollment.installments
        )
    )
    snapshot = {
        "old_cohort_id": str(enrollment.cohort_id),
        "target_cohort_id": str(target.id),
        "old_price_kobo": enrollment.price_snapshot_amount,
        "target_base_price_kobo": _resolve_enrollment_total_fee(program, target),
        "payment_references": financial.get("references", []),
        "payment_statuses": financial.get("statuses", []),
        "requires_financial_review": bool(
            local_money or financial.get("has_payment_activity")
        ),
    }
    # A financial hold is recorded, not silently overridden or automatically
    # reallocated. Reviewers handle shared-transfer evidence separately.
    if snapshot["requires_financial_review"] or enrollment.progress_records:
        change = AcademyEnrollmentChange(
            journey_id=journey.id,
            from_enrollment_id=enrollment.id,
            target_cohort_id=target.id,
            actor_auth_id=current_user.user_id,
            state="needs_review",
            snapshot=snapshot,
        )
        db.add(change)
        await db.commit()
        return ChangeCohortResult(
            state="needs_review",
            change_id=change.id,
            message="Your cohort change has been recorded for review. Existing payment or progress records were preserved.",
        )
    count = (
        await db.execute(
            select(func.count(Enrollment.id)).where(
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [EnrollmentStatus.PENDING_APPROVAL, EnrollmentStatus.ENROLLED]
                ),
            )
        )
    ).scalar_one()
    if target.capacity is not None and count >= target.capacity:
        raise HTTPException(409, "The selected cohort is full")
    other = (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.member_id == enrollment.member_id,
                Enrollment.program_id == enrollment.program_id,
                Enrollment.cohort_id == target.id,
                Enrollment.status.in_(
                    [
                        EnrollmentStatus.PENDING_APPROVAL,
                        EnrollmentStatus.ENROLLED,
                        EnrollmentStatus.WAITLIST,
                    ]
                ),
            )
        )
    ).scalar_one_or_none()
    if other:
        raise HTTPException(
            409, "You already have an enrollment in the destination cohort"
        )
    enrollment.status = EnrollmentStatus.DROPPED
    enrollment.dropped_at = now
    replacement = Enrollment(
        member_id=enrollment.member_id,
        member_auth_id=enrollment.member_auth_id,
        program_id=enrollment.program_id,
        cohort_id=target.id,
        preferences=dict(enrollment.preferences or {}),
        status=EnrollmentStatus.PENDING_APPROVAL,
        payment_status=PaymentStatus.PENDING,
        price_snapshot_amount=_resolve_enrollment_total_fee(program, target),
        currency_snapshot=program.currency or "NGN",
        membership_policy_snapshot=_resolve_enrollment_membership_policy(
            program, target
        ),
        uses_installments=False,
    )
    db.add(replacement)
    await db.flush()
    await _sync_installment_state_for_enrollment(db, replacement)
    change = AcademyEnrollmentChange(
        journey_id=journey.id,
        from_enrollment_id=enrollment.id,
        to_enrollment_id=replacement.id,
        target_cohort_id=target.id,
        actor_auth_id=current_user.user_id,
        state="completed",
        snapshot=snapshot,
    )
    db.add(change)
    await db.commit()
    return ChangeCohortResult(
        state="completed",
        enrollment_id=replacement.id,
        change_id=change.id,
        message="Cohort changed. Please review the new price and payment options before paying.",
    )
