"""Shared Refund API routers — Admin, Member, and Internal service-to-service (ADR-024)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import (
    get_current_user,
    require_admin,
    require_service_role,
)
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.payments_service.models import (
    Payment,
    PaymentPurpose,
    PaymentStatus,
    Refund,
    RefundStatus,
)
from services.payments_service.routers.intents import (
    _mark_paid_and_apply,
    _paystack_enabled,
    _verify_paystack_transaction,
)
from services.payments_service.schemas.refund import (
    CloseUnpaidStorePaymentResponse,
    PaymentReferenceStatusResponse,
    RefundApproveRequest,
    RefundCompleteManualRequest,
    RefundCreateRequest,
    RefundFailOrCancelRequest,
    RefundInitiatePaystackRequest,
    RefundListResponse,
    RefundResponse,
    RefundVerifyBankRequest,
)
from services.payments_service.services.ledger_emit import to_kobo
from services.payments_service.services.paystack_client import (
    PaystackError,
    get_paystack_client,
)
from services.payments_service.services.refund_service import (
    approve_refund,
    complete_manual_refund,
    create_refund_record,
    get_active_refunded_cash_kobo,
    initiate_paystack_refund,
    notify_source_service_refund_status,
    sync_paystack_refund_status,
)

logger = get_logger(__name__)

# Mounted under /payments in app/main.py → /api/v1/payments/admin/refunds/*
admin_router = APIRouter(prefix="/admin/refunds", tags=["admin-refunds"])
# Mounted under /payments in app/main.py → /api/v1/payments/me/refunds
member_router = APIRouter(prefix="/me/refunds", tags=["member-refunds"])
# Mounted at root in app/main.py → /internal/payments/...
internal_router = APIRouter(prefix="/internal/payments", tags=["internal-refunds"])


# ============================================================================
# ADMIN ROUTES
# ============================================================================


@admin_router.get("/banks")
async def list_refund_banks(
    current_user: AuthUser = Depends(require_admin),
):
    """List Nigerian banks from Paystack for manual bank-transfer refund verification."""
    try:
        client = get_paystack_client()
        banks = await client.list_banks(country="nigeria")
        return [
            {"name": b.name, "code": b.code, "slug": b.slug}
            for b in banks
            if b.is_active
        ]
    except (PaystackError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch bank list: {exc}",
        ) from exc


@admin_router.get("", response_model=RefundListResponse)
async def list_refunds_admin(
    status_filter: Optional[RefundStatus] = Query(default=None, alias="status"),
    source_service: Optional[str] = None,
    provider: Optional[str] = None,
    payment_reference: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List shared refunds with filterable queue and cash summary totals."""
    query = select(Refund)
    if status_filter is not None:
        query = query.where(Refund.status == status_filter)
    if source_service:
        query = query.where(Refund.source_service == source_service)
    if provider:
        query = query.where(Refund.provider == provider)
    if payment_reference:
        query = query.where(Refund.payment_reference == payment_reference)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0

    rows = (
        (
            await db.execute(
                query.order_by(Refund.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    pending_kobo = (
        await db.execute(
            select(func.coalesce(func.sum(Refund.cash_refund_kobo), 0)).where(
                Refund.status.in_((RefundStatus.REQUESTED, RefundStatus.APPROVED))
            )
        )
    ).scalar_one() or 0
    initiated_kobo = (
        await db.execute(
            select(func.coalesce(func.sum(Refund.cash_refund_kobo), 0)).where(
                Refund.status == RefundStatus.INITIATED
            )
        )
    ).scalar_one() or 0
    completed_kobo = (
        await db.execute(
            select(func.coalesce(func.sum(Refund.cash_refund_kobo), 0)).where(
                Refund.status == RefundStatus.COMPLETED
            )
        )
    ).scalar_one() or 0

    return RefundListResponse(
        items=[RefundResponse.model_validate(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
        pending_cash_kobo=int(pending_kobo),
        initiated_cash_kobo=int(initiated_kobo),
        completed_cash_kobo=int(completed_kobo),
    )


@admin_router.get("/{refund_id}", response_model=RefundResponse)
async def get_refund_admin(
    refund_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Fetch a single refund record (admin)."""
    refund = await db.get(Refund, refund_id)
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    return refund


@admin_router.post("/{refund_id}/approve", response_model=RefundResponse)
async def approve_refund_admin(
    refund_id: uuid.UUID,
    payload: RefundApproveRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a requested refund (accrues Refunds Payable in the ledger)."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    return await approve_refund(
        db,
        refund,
        actor_auth_id=current_user.user_id,
        admin_note=payload.admin_note,
        override_reason=payload.override_reason,
        auto_initiate_paystack=payload.auto_initiate_paystack,
    )


@admin_router.post("/{refund_id}/initiate-paystack", response_model=RefundResponse)
async def initiate_paystack_refund_admin(
    refund_id: uuid.UUID,
    payload: RefundInitiatePaystackRequest = RefundInitiatePaystackRequest(),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Initiate (or idempotently return) a Paystack API refund."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    return await initiate_paystack_refund(
        db,
        refund,
        actor_auth_id=current_user.user_id,
        customer_note=payload.customer_note,
        merchant_note=payload.merchant_note,
    )


@admin_router.post("/{refund_id}/sync-paystack", response_model=RefundResponse)
async def sync_paystack_refund_admin(
    refund_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Poll Paystack GET /refund/{id} to reconcile refund status."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    return await sync_paystack_refund_status(db, refund)


@admin_router.post("/{refund_id}/verify-bank-account", response_model=RefundResponse)
async def verify_refund_bank_account_admin(
    refund_id: uuid.UUID,
    payload: RefundVerifyBankRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Verify recipient NUBAN via Paystack resolve_account before bank-transfer refund."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    if refund.status == RefundStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Refund is already completed")

    try:
        client = get_paystack_client()
        resolved = await client.resolve_account(
            payload.account_number, payload.bank_code
        )
    except (PaystackError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bank account verification failed: {exc}",
        ) from exc

    refund.recipient_bank_code = payload.bank_code
    if payload.bank_name:
        refund.recipient_bank_name = payload.bank_name
    refund.recipient_account_number = resolved.account_number
    refund.recipient_account_name = resolved.account_name
    refund.recipient_account_verified = True
    await db.commit()
    await db.refresh(refund)
    return refund


@admin_router.post("/{refund_id}/complete-manual", response_model=RefundResponse)
async def complete_manual_refund_admin(
    refund_id: uuid.UUID,
    payload: RefundCompleteManualRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Confirm manual bank-transfer refund disbursement with a verified bank reference."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    return await complete_manual_refund(
        db,
        refund,
        actor_auth_id=current_user.user_id,
        external_bank_reference=payload.external_bank_reference,
        admin_note=payload.admin_note,
    )


@admin_router.post("/{refund_id}/fail-or-cancel", response_model=RefundResponse)
async def fail_or_cancel_refund_admin(
    refund_id: uuid.UUID,
    payload: RefundFailOrCancelRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel an uninitiated refund or mark a disbursement failed."""
    refund = (
        await db.execute(select(Refund).where(Refund.id == refund_id).with_for_update())
    ).scalar_one_or_none()
    if not refund:
        raise HTTPException(status_code=404, detail="Refund not found")
    if refund.status == RefundStatus.COMPLETED:
        raise HTTPException(
            status_code=409, detail="Cannot cancel or fail a completed refund"
        )
    if payload.action == "cancel":
        if refund.status not in (RefundStatus.REQUESTED, RefundStatus.APPROVED):
            raise HTTPException(
                status_code=409,
                detail="Only requested or approved refunds can be cancelled",
            )
        refund.status = RefundStatus.CANCELLED
        refund.admin_note = payload.reason
    else:
        refund.status = RefundStatus.FAILED
        refund.failed_at = utc_now()
        refund.failure_reason = payload.reason

    await db.commit()
    await db.refresh(refund)
    await notify_source_service_refund_status(refund)
    return refund


# ============================================================================
# MEMBER ROUTES
# ============================================================================


@member_router.get("", response_model=list[RefundResponse])
async def list_my_refunds(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List the authenticated member's own refunds with live status tracking."""
    rows = (
        (
            await db.execute(
                select(Refund)
                .where(Refund.member_auth_id == current_user.user_id)
                .order_by(Refund.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# ============================================================================
# INTERNAL SERVICE-TO-SERVICE ROUTES
# ============================================================================


@internal_router.post(
    "/refunds",
    response_model=RefundResponse,
    dependencies=[Depends(require_service_role)],
    include_in_schema=False,
)
async def internal_create_refund(
    payload: RefundCreateRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a shared refund record from an owning domain service."""
    return await create_refund_record(db, payload)


@internal_router.get(
    "/refunds/by-source/{source_service}/{source_entity_id}",
    response_model=list[RefundResponse],
    dependencies=[Depends(require_service_role)],
    include_in_schema=False,
)
async def internal_list_refunds_by_source(
    source_service: str,
    source_entity_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Fetch all refunds attached to a source domain entity (e.g. store order ID)."""
    rows = (
        (
            await db.execute(
                select(Refund)
                .where(
                    Refund.source_service == source_service,
                    Refund.source_entity_id == source_entity_id,
                )
                .order_by(Refund.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@internal_router.get(
    "/by-reference/{reference}/status",
    response_model=PaymentReferenceStatusResponse,
    dependencies=[Depends(require_service_role)],
    include_in_schema=False,
)
async def internal_get_payment_reference_status(
    reference: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Return authoritative payment status, proof state, and refundable balance."""
    payment = (
        await db.execute(select(Payment).where(Payment.reference == reference))
    ).scalar_one_or_none()
    if payment is None:
        return PaymentReferenceStatusResponse(
            exists=False,
            reference=reference,
            can_self_cancel_unpaid=True,
        )

    has_proof = bool(
        payment.proof_of_payment_media_id is not None
        or payment.status == PaymentStatus.PENDING_REVIEW
    )
    amount_kobo = to_kobo(payment.amount)
    refunded_kobo = await get_active_refunded_cash_kobo(db, payment.id)
    refundable_kobo = (
        max(0, amount_kobo - refunded_kobo)
        if payment.status == PaymentStatus.PAID
        else 0
    )

    can_cancel_unpaid = True
    block_reason: Optional[str] = None
    if payment.status == PaymentStatus.PAID:
        can_cancel_unpaid = False
        block_reason = "Payment has already settled"
    elif has_proof:
        can_cancel_unpaid = False
        block_reason = (
            "A bank transfer receipt is currently under admin review; "
            "request cancellation instead so finance can reconcile your transfer."
        )

    return PaymentReferenceStatusResponse(
        exists=True,
        reference=payment.reference,
        status=payment.status.value,
        payment_method=payment.payment_method,
        provider=payment.provider,
        amount_kobo=amount_kobo,
        paid_at=payment.paid_at,
        has_proof=has_proof,
        refunded_cash_kobo=refunded_kobo,
        refundable_cash_kobo=refundable_kobo,
        can_self_cancel_unpaid=can_cancel_unpaid,
        block_reason=block_reason,
    )


@internal_router.post(
    "/by-reference/{reference}/close-unpaid",
    response_model=CloseUnpaidStorePaymentResponse,
    dependencies=[Depends(require_service_role)],
    include_in_schema=False,
)
async def internal_close_unpaid_store_payment(
    reference: str,
    db: AsyncSession = Depends(get_async_db),
):
    """Safely close an unpaid STORE_ORDER payment after live Paystack check under lock.

    Prevents cancelling an order whose Paystack payment just succeeded or whose
    manual bank-transfer proof is pending admin review (ADR-003, ADR-007, ADR-024).
    """
    payment = (
        await db.execute(
            select(Payment).where(Payment.reference == reference).with_for_update()
        )
    ).scalar_one_or_none()

    if payment is None:
        return CloseUnpaidStorePaymentResponse(
            closed=True,
            reference=reference,
            status="not_initialized",
            detail="No payment record was initialized for this reference",
        )

    if payment.purpose != PaymentPurpose.STORE_ORDER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment reference is not a store order payment",
        )

    if payment.status == PaymentStatus.PAID or payment.entitlement_applied_at:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment has already been confirmed; refresh your order before cancelling",
        )

    if (
        payment.status == PaymentStatus.PENDING_REVIEW
        or payment.proof_of_payment_media_id is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A bank transfer receipt is pending admin review. "
                "Please submit a cancellation request so our team can reconcile your transfer."
            ),
        )

    if payment.status == PaymentStatus.FAILED:
        return CloseUnpaidStorePaymentResponse(
            closed=True,
            reference=reference,
            status="failed",
            detail="Payment attempt was already closed",
        )

    # Live Paystack verification for pending online attempts before closing.
    if (payment.payment_method or "paystack") == "paystack" and _paystack_enabled():
        try:
            ps_data = await _verify_paystack_transaction(payment.reference)
        except HTTPException:
            ps_data = None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Live Paystack verify failed while closing unpaid store payment %s: %s",
                reference,
                exc,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify payment status with Paystack; please retry shortly",
            ) from exc

        if isinstance(ps_data, dict):
            ps_status = str(ps_data.get("status") or "").lower()
            if ps_status == "success":
                await _mark_paid_and_apply(
                    db=db,
                    payment=payment,
                    provider="paystack",
                    provider_reference=payment.reference,
                    paid_at=None,
                    provider_payload={"source": "cancel_preflight", "data": ps_data},
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Your Paystack payment already succeeded! Refresh the order to view its confirmed status.",
                )

    payment.status = PaymentStatus.FAILED
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "closed_by_order_cancel": {
            "at": utc_now().isoformat(),
            "reason": "unpaid_store_order_cancelled",
        },
    }
    await db.commit()
    return CloseUnpaidStorePaymentResponse(
        closed=True,
        reference=reference,
        status="failed",
        detail="Unpaid payment intent closed",
    )
