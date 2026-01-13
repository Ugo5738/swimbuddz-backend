"""Payout API routes for coach payout management."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from sqlalchemy import func, select

from .models import CoachPayout, PayoutMethod, PayoutStatus
from .payout_schemas import (
    PayoutApprove,
    PayoutCompleteManual,
    PayoutCreate,
    PayoutFail,
    PayoutListResponse,
    PayoutResponse,
    PayoutSummary,
)
from .paystack_client import PaystackClient, PaystackError

logger = get_logger(__name__)

# Admin router for payout management
admin_router = APIRouter(prefix="/admin/payouts", tags=["admin-payouts"])

# Coach router for viewing own payouts
coach_router = APIRouter(prefix="/coach/me/payouts", tags=["coach-payouts"])


def _payout_to_response(payout: CoachPayout) -> PayoutResponse:
    """Convert CoachPayout model to response schema."""
    return PayoutResponse(
        id=str(payout.id),
        coach_member_id=str(payout.coach_member_id),
        period_start=payout.period_start,
        period_end=payout.period_end,
        period_label=payout.period_label,
        academy_earnings=payout.academy_earnings,
        session_earnings=payout.session_earnings,
        other_earnings=payout.other_earnings,
        total_amount=payout.total_amount,
        currency=payout.currency,
        status=payout.status,
        payout_method=payout.payout_method,
        approved_by=payout.approved_by,
        approved_at=payout.approved_at,
        paid_at=payout.paid_at,
        payment_reference=payout.payment_reference,
        paystack_transfer_code=payout.paystack_transfer_code,
        paystack_transfer_status=payout.paystack_transfer_status,
        admin_notes=payout.admin_notes,
        failure_reason=payout.failure_reason,
        created_at=payout.created_at,
        updated_at=payout.updated_at,
    )


# =============================================================================
# Admin Endpoints
# =============================================================================


@admin_router.get("/", response_model=PayoutListResponse)
async def list_payouts(
    status: Optional[PayoutStatus] = None,
    coach_member_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _admin: AuthUser = Depends(require_admin),
):
    """List all payouts with optional filters."""
    async with AsyncSessionLocal() as session:
        query = select(CoachPayout)

        if status:
            query = query.where(CoachPayout.status == status)
        if coach_member_id:
            query = query.where(CoachPayout.coach_member_id == coach_member_id)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_query) or 0

        # Paginate
        query = query.order_by(CoachPayout.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        payouts = result.scalars().all()

        return PayoutListResponse(
            items=[_payout_to_response(p) for p in payouts],
            total=total,
            page=page,
            page_size=page_size,
        )


@admin_router.get("/summary", response_model=PayoutSummary)
async def get_payout_summary(
    _admin: AuthUser = Depends(require_admin),
):
    """Get summary stats for all payouts."""
    async with AsyncSessionLocal() as session:
        # Count by status
        pending = (
            await session.scalar(
                select(func.count()).where(CoachPayout.status == PayoutStatus.PENDING)
            )
            or 0
        )
        approved = (
            await session.scalar(
                select(func.count()).where(CoachPayout.status == PayoutStatus.APPROVED)
            )
            or 0
        )
        paid = (
            await session.scalar(
                select(func.count()).where(CoachPayout.status == PayoutStatus.PAID)
            )
            or 0
        )
        failed = (
            await session.scalar(
                select(func.count()).where(CoachPayout.status == PayoutStatus.FAILED)
            )
            or 0
        )

        # Sum pending amount
        pending_amount = (
            await session.scalar(
                select(func.coalesce(func.sum(CoachPayout.total_amount), 0)).where(
                    CoachPayout.status.in_(
                        [PayoutStatus.PENDING, PayoutStatus.APPROVED]
                    )
                )
            )
            or 0
        )

        # Sum paid amount
        paid_amount = (
            await session.scalar(
                select(func.coalesce(func.sum(CoachPayout.total_amount), 0)).where(
                    CoachPayout.status == PayoutStatus.PAID
                )
            )
            or 0
        )

        return PayoutSummary(
            total_pending=pending,
            total_approved=approved,
            total_paid=paid,
            total_failed=failed,
            pending_amount=pending_amount,
            paid_amount=paid_amount,
        )


@admin_router.post("/", response_model=PayoutResponse)
async def create_payout(
    data: PayoutCreate,
    admin: AuthUser = Depends(require_admin),
):
    """Create a new payout for a coach."""
    async with AsyncSessionLocal() as session:
        total = data.academy_earnings + data.session_earnings + data.other_earnings

        payout = CoachPayout(
            coach_member_id=data.coach_member_id,
            period_start=data.period_start,
            period_end=data.period_end,
            period_label=data.period_label,
            academy_earnings=data.academy_earnings,
            session_earnings=data.session_earnings,
            other_earnings=data.other_earnings,
            total_amount=total,
            status=PayoutStatus.PENDING,
            admin_notes=data.admin_notes,
        )

        session.add(payout)
        await session.commit()
        await session.refresh(payout)

        logger.info(
            f"Created payout {payout.id} for coach {data.coach_member_id}",
            extra={"extra_fields": {"amount": total, "period": data.period_label}},
        )

        return _payout_to_response(payout)


@admin_router.get("/{payout_id}", response_model=PayoutResponse)
async def get_payout(
    payout_id: UUID,
    _admin: AuthUser = Depends(require_admin),
):
    """Get a single payout by ID."""
    async with AsyncSessionLocal() as session:
        payout = await session.get(CoachPayout, payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")
        return _payout_to_response(payout)


@admin_router.put("/{payout_id}/approve", response_model=PayoutResponse)
async def approve_payout(
    payout_id: UUID,
    data: PayoutApprove,
    admin: AuthUser = Depends(require_admin),
):
    """Approve a pending payout."""
    async with AsyncSessionLocal() as session:
        payout = await session.get(CoachPayout, payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        if payout.status != PayoutStatus.PENDING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve payout with status: {payout.status.value}",
            )

        payout.status = PayoutStatus.APPROVED
        payout.approved_by = admin.email or admin.user_id
        payout.approved_at = datetime.now(timezone.utc)
        if data.admin_notes:
            payout.admin_notes = data.admin_notes

        await session.commit()
        await session.refresh(payout)

        logger.info(f"Payout {payout_id} approved by {payout.approved_by}")

        return _payout_to_response(payout)


@admin_router.post("/{payout_id}/initiate-transfer", response_model=PayoutResponse)
async def initiate_transfer(
    payout_id: UUID,
    admin: AuthUser = Depends(require_admin),
):
    """
    Initiate a Paystack transfer for an approved payout.

    Requires the coach to have a verified bank account with Paystack recipient code.
    """
    # Import here to avoid circular dependency
    from services.members_service.models import CoachBankAccount

    async with AsyncSessionLocal() as session:
        payout = await session.get(CoachPayout, payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        if payout.status not in [PayoutStatus.APPROVED, PayoutStatus.FAILED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot initiate transfer for payout with status: {payout.status.value}",
            )

        # Get coach's bank account
        bank_result = await session.execute(
            select(CoachBankAccount).where(
                CoachBankAccount.member_id == payout.coach_member_id
            )
        )
        bank_account = bank_result.scalar_one_or_none()

        if not bank_account:
            raise HTTPException(
                status_code=400, detail="Coach has no bank account on file"
            )

        if not bank_account.paystack_recipient_code:
            raise HTTPException(
                status_code=400,
                detail="Coach bank account does not have a Paystack recipient code",
            )

        # Initiate transfer
        paystack = PaystackClient()
        try:
            reference = CoachPayout.generate_reference()
            transfer = await paystack.initiate_transfer(
                recipient_code=bank_account.paystack_recipient_code,
                amount_kobo=payout.total_amount,
                reason=f"SwimBuddz Coach Payout - {payout.period_label}",
                reference=reference,
            )

            payout.status = PayoutStatus.PROCESSING
            payout.payout_method = PayoutMethod.PAYSTACK_TRANSFER
            payout.payment_reference = reference
            payout.paystack_transfer_code = transfer.transfer_code
            payout.paystack_transfer_status = transfer.status

            await session.commit()
            await session.refresh(payout)

            logger.info(
                f"Initiated Paystack transfer for payout {payout_id}",
                extra={
                    "extra_fields": {
                        "transfer_code": transfer.transfer_code,
                        "reference": reference,
                        "amount": payout.total_amount,
                    }
                },
            )

            return _payout_to_response(payout)

        except PaystackError as e:
            logger.error(
                f"Paystack transfer failed for payout {payout_id}: {e.message}",
                extra={"extra_fields": {"response": e.response_data}},
            )
            raise HTTPException(status_code=500, detail=f"Transfer failed: {e.message}")


@admin_router.put("/{payout_id}/complete-manual", response_model=PayoutResponse)
async def complete_manual_payout(
    payout_id: UUID,
    data: PayoutCompleteManual,
    admin: AuthUser = Depends(require_admin),
):
    """Mark a payout as completed via manual bank transfer or other method."""
    async with AsyncSessionLocal() as session:
        payout = await session.get(CoachPayout, payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        if payout.status not in [
            PayoutStatus.APPROVED,
            PayoutStatus.PENDING,
            PayoutStatus.FAILED,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete payout with status: {payout.status.value}",
            )

        payout.status = PayoutStatus.PAID
        payout.payout_method = data.payout_method
        payout.payment_reference = data.payment_reference
        payout.paid_at = datetime.now(timezone.utc)
        if data.admin_notes:
            payout.admin_notes = data.admin_notes

        # Ensure approved fields are set
        if not payout.approved_by:
            payout.approved_by = admin.email or admin.user_id
            payout.approved_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(payout)

        logger.info(
            f"Payout {payout_id} marked as manually paid",
            extra={
                "extra_fields": {
                    "method": data.payout_method.value,
                    "reference": data.payment_reference,
                }
            },
        )

        return _payout_to_response(payout)


@admin_router.put("/{payout_id}/fail", response_model=PayoutResponse)
async def fail_payout(
    payout_id: UUID,
    data: PayoutFail,
    admin: AuthUser = Depends(require_admin),
):
    """Mark a payout as failed."""
    async with AsyncSessionLocal() as session:
        payout = await session.get(CoachPayout, payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Payout not found")

        if payout.status == PayoutStatus.PAID:
            raise HTTPException(
                status_code=400,
                detail="Cannot fail a payout that has already been paid",
            )

        payout.status = PayoutStatus.FAILED
        payout.failure_reason = data.failure_reason
        if data.admin_notes:
            payout.admin_notes = data.admin_notes

        await session.commit()
        await session.refresh(payout)

        logger.info(f"Payout {payout_id} marked as failed: {data.failure_reason}")

        return _payout_to_response(payout)


# =============================================================================
# Coach Endpoints (View own payouts)
# =============================================================================


@coach_router.get("/", response_model=PayoutListResponse)
async def get_my_payouts(
    status: Optional[PayoutStatus] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
):
    """Get current coach's payouts."""
    # Import here to avoid circular dependency
    from services.members_service.models import Member

    async with AsyncSessionLocal() as session:
        # Get member ID from auth
        member_result = await session.execute(
            select(Member).where(Member.auth_id == current_user.user_id)
        )
        member = member_result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        query = select(CoachPayout).where(CoachPayout.coach_member_id == member.id)

        if status:
            query = query.where(CoachPayout.status == status)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = await session.scalar(count_query) or 0

        # Paginate
        query = query.order_by(CoachPayout.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await session.execute(query)
        payouts = result.scalars().all()

        return PayoutListResponse(
            items=[_payout_to_response(p) for p in payouts],
            total=total,
            page=page,
            page_size=page_size,
        )


@coach_router.get("/{payout_id}", response_model=PayoutResponse)
async def get_my_payout(
    payout_id: UUID,
    current_user: AuthUser = Depends(get_current_user),
):
    """Get a specific payout for the current coach."""
    from services.members_service.models import Member

    async with AsyncSessionLocal() as session:
        member_result = await session.execute(
            select(Member).where(Member.auth_id == current_user.user_id)
        )
        member = member_result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        payout = await session.get(CoachPayout, payout_id)

        if not payout or payout.coach_member_id != member.id:
            raise HTTPException(status_code=404, detail="Payout not found")

        return _payout_to_response(payout)
