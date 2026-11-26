"""Shared refund domain logic (ADR-023).

Enforces:
- Row-locked over-refund protection: SUM(active refunds.cash_refund_kobo) <= paid_kobo.
- Idempotency on creation, approval, Paystack initiation, and completion.
- Paystack 1-year refund window check (falls back to manual bank transfer if older).
- Two-step double-entry ledger emissions (DR revenue / CR refunds_payable -> DR refunds_payable / CR clearing|bank).
- Source-service status callback notifications (e.g. store_service).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from services.payments_service.models import (
    Payment,
    PaymentStatus,
    Refund,
    RefundMethod,
    RefundStatus,
)
from services.payments_service.schemas.refund import RefundCreateRequest
from services.payments_service.services.ledger_emit import (
    emit_refund_approved_to_ledger,
    emit_refund_completed_to_ledger,
    to_kobo,
)
from services.payments_service.services.paystack_client import (
    PaystackError,
    get_paystack_client,
)

logger = get_logger(__name__)

# Paystack enforces a 1-year transaction age limit for API refunds (effective 15 Oct 2026).
PAYSTACK_REFUND_MAX_AGE_DAYS = 365

ACTIVE_REFUND_STATUSES = (
    RefundStatus.REQUESTED,
    RefundStatus.APPROVED,
    RefundStatus.INITIATED,
    RefundStatus.COMPLETED,
)


def resolve_refund_method(
    payment: Payment | None,
    *,
    cash_refund_kobo: int,
    bubbles_refund: int,
    store_credit_refund_kobo: int,
    preferred_method: RefundMethod | None = None,
) -> tuple[RefundMethod, str | None, bool]:
    """Determine refund channel, provider string, and whether Paystack age cap forced fallback."""
    tenders = sum(
        1
        for val in (cash_refund_kobo, bubbles_refund, store_credit_refund_kobo)
        if val > 0
    )
    if cash_refund_kobo <= 0:
        if tenders > 1:
            return RefundMethod.MIXED, "non_cash", False
        if bubbles_refund > 0:
            return RefundMethod.WALLET_BUBBLES, "wallet", False
        return RefundMethod.STORE_CREDIT, "store_credit", False

    # Cash component exists — decide between Paystack API vs manual bank transfer.
    age_fallback = False
    is_paystack_payment = bool(
        payment
        and (
            (payment.provider or "").lower() == "paystack"
            or (payment.payment_method or "").lower() == "paystack"
        )
    )
    if is_paystack_payment and payment and payment.paid_at:
        age = utc_now() - payment.paid_at
        if age > timedelta(days=PAYSTACK_REFUND_MAX_AGE_DAYS):
            age_fallback = True
            is_paystack_payment = False

    if preferred_method == RefundMethod.MANUAL_BANK_TRANSFER:
        provider = "manual_transfer"
        method = (
            RefundMethod.MIXED if tenders > 1 else RefundMethod.MANUAL_BANK_TRANSFER
        )
        return method, provider, age_fallback

    if is_paystack_payment:
        provider = "paystack"
        method = RefundMethod.MIXED if tenders > 1 else RefundMethod.PAYSTACK_REFUND
        return method, provider, False

    provider = "manual_transfer"
    method = RefundMethod.MIXED if tenders > 1 else RefundMethod.MANUAL_BANK_TRANSFER
    return method, provider, age_fallback


async def get_active_refunded_cash_kobo(
    db: AsyncSession, payment_id
) -> int:
    """Return total active (non-failed, non-cancelled) cash kobo already allocated to refunds."""
    result = await db.execute(
        select(func.coalesce(func.sum(Refund.cash_refund_kobo), 0)).where(
            Refund.payment_id == payment_id,
            Refund.status.in_(ACTIVE_REFUND_STATUSES),
        )
    )
    return int(result.scalar_one() or 0)


async def notify_source_service_refund_status(refund: Refund) -> None:
    """Best-effort callback to the owning domain service when refund status changes."""
    if refund.source_service != "store":
        return
    settings = get_settings()
    try:
        resp = await internal_post(
            service_url=settings.STORE_SERVICE_URL,
            path="/internal/store/orders/refund-status-callback",
            calling_service="payments",
            json={
                "refund_reference": refund.refund_reference,
                "source_entity_type": refund.source_entity_type,
                "source_entity_id": refund.source_entity_id,
                "payment_reference": refund.payment_reference,
                "status": refund.status.value,
                "cash_refund_kobo": refund.cash_refund_kobo,
                "bubbles_refund": refund.bubbles_refund,
                "store_credit_refund_kobo": refund.store_credit_refund_kobo,
                "provider": refund.provider,
                "provider_refund_id": refund.provider_refund_id,
                "external_bank_reference": refund.external_bank_reference,
                "failure_reason": refund.failure_reason,
                "completed_at": (
                    refund.completed_at.isoformat() if refund.completed_at else None
                ),
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Store refund callback returned %d for %s: %s",
                resp.status_code,
                refund.refund_reference,
                resp.text,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to notify store_service for refund %s: %s",
            refund.refund_reference,
            exc,
        )


async def create_refund_record(
    db: AsyncSession,
    payload: RefundCreateRequest,
) -> Refund:
    """Create a Refund under Payment row lock, enforcing idempotency & paid-amount cap."""
    existing = (
        await db.execute(
            select(Refund).where(Refund.idempotency_key == payload.idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    payment: Optional[Payment] = None
    if payload.payment_reference:
        payment = (
            await db.execute(
                select(Payment)
                .where(Payment.reference == payload.payment_reference)
                .with_for_update()
            )
        ).scalar_one_or_none()

    if payload.cash_refund_kobo > 0:
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Original payment {payload.payment_reference} not found for cash refund",
            )
        if payment.status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot issue cash refund for payment in status '{payment.status.value}'",
            )
        paid_kobo = to_kobo(payment.amount)
        already_refunded = await get_active_refunded_cash_kobo(db, payment.id)
        if already_refunded + payload.cash_refund_kobo > paid_kobo:
            remaining = max(0, paid_kobo - already_refunded)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cash refund ({payload.cash_refund_kobo} kobo) exceeds remaining "
                    f"refundable payment balance ({remaining} kobo)"
                ),
            )

    method, provider, age_fallback = resolve_refund_method(
        payment,
        cash_refund_kobo=payload.cash_refund_kobo,
        bubbles_refund=payload.bubbles_refund,
        store_credit_refund_kobo=payload.store_credit_refund_kobo,
        preferred_method=payload.preferred_method,
    )

    now = utc_now()
    meta = dict(payload.metadata or {})
    if age_fallback:
        meta["paystack_age_fallback"] = (
            "Original Paystack transaction exceeds 1-year API refund window; "
            "routed to verified manual bank transfer."
        )

    # Non-cash refunds (Store Credit / Bubbles already restored by owning service)
    # complete immediately when auto_approve=True.
    initial_status = RefundStatus.REQUESTED
    approved_at = None
    completed_at = None
    if payload.auto_approve:
        if payload.cash_refund_kobo == 0:
            initial_status = RefundStatus.COMPLETED
            approved_at = now
            completed_at = now
        else:
            initial_status = RefundStatus.APPROVED
            approved_at = now

    refund = Refund(
        refund_reference=Refund.generate_reference(),
        idempotency_key=payload.idempotency_key,
        payment_id=payment.id if payment else None,
        payment_reference=payment.reference if payment else payload.payment_reference,
        member_auth_id=payload.member_auth_id,
        payer_email=payload.payer_email or (payment.payer_email if payment else None),
        source_service=payload.source_service,
        source_entity_type=payload.source_entity_type,
        source_entity_id=payload.source_entity_id,
        purpose=payload.purpose,
        currency=payload.currency,
        requested_total_kobo=payload.requested_total_kobo,
        cash_refund_kobo=payload.cash_refund_kobo,
        bubbles_refund=payload.bubbles_refund,
        store_credit_refund_kobo=payload.store_credit_refund_kobo,
        refund_method=method,
        status=initial_status,
        reason=payload.reason,
        admin_note=payload.admin_note,
        override_reason=payload.override_reason,
        provider=provider,
        recipient_bank_code=payload.recipient_bank_code,
        recipient_bank_name=payload.recipient_bank_name,
        recipient_account_number=payload.recipient_account_number,
        recipient_account_name=payload.recipient_account_name,
        wallet_refund_transaction_id=payload.wallet_refund_transaction_id,
        store_credit_id=payload.store_credit_id,
        requested_by_auth_id=payload.requested_by_auth_id,
        approved_by_auth_id=payload.approved_by_auth_id if payload.auto_approve else None,
        approved_at=approved_at,
        completed_by_auth_id=(
            payload.approved_by_auth_id if initial_status == RefundStatus.COMPLETED else None
        ),
        completed_at=completed_at,
        refund_metadata=meta or None,
    )
    db.add(refund)
    await db.commit()
    await db.refresh(refund)

    if initial_status == RefundStatus.APPROVED and refund.cash_refund_kobo > 0:
        posted = await emit_refund_approved_to_ledger(db, refund)
        if posted:
            refund.ledger_accrual_posted = True
            await db.commit()
            await db.refresh(refund)

    if (
        payload.auto_initiate_paystack
        and refund.status == RefundStatus.APPROVED
        and refund.provider == "paystack"
        and refund.cash_refund_kobo > 0
    ):
        try:
            refund = await initiate_paystack_refund(
                db,
                refund,
                actor_auth_id=payload.approved_by_auth_id
                or payload.requested_by_auth_id
                or "system",
                customer_note=payload.reason or f"Refund {refund.refund_reference}",
                merchant_note=f"{refund.source_service}:{refund.source_entity_id}",
            )
        except HTTPException as exc:
            # Keep the approved Refund row intact so ops can retry initiation safely.
            logger.warning(
                "Auto-initiate Paystack refund failed for %s (kept APPROVED for retry): %s",
                refund.refund_reference,
                exc.detail,
            )

    return refund


async def approve_refund(
    db: AsyncSession,
    refund: Refund,
    *,
    actor_auth_id: str,
    admin_note: str | None = None,
    override_reason: str | None = None,
    auto_initiate_paystack: bool = False,
) -> Refund:
    """Approve a REQUESTED refund and accrue Refunds Payable in the ledger."""
    if refund.status == RefundStatus.APPROVED:
        return refund
    if refund.status != RefundStatus.REQUESTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve refund in status '{refund.status.value}'",
        )

    now = utc_now()
    if admin_note:
        refund.admin_note = admin_note
    if override_reason:
        refund.override_reason = override_reason
    refund.approved_by_auth_id = actor_auth_id
    refund.approved_at = now

    if refund.cash_refund_kobo == 0:
        refund.status = RefundStatus.COMPLETED
        refund.completed_by_auth_id = actor_auth_id
        refund.completed_at = now
    else:
        refund.status = RefundStatus.APPROVED

    await db.commit()
    await db.refresh(refund)

    if refund.cash_refund_kobo > 0 and not refund.ledger_accrual_posted:
        posted = await emit_refund_approved_to_ledger(db, refund)
        if posted:
            refund.ledger_accrual_posted = True
            await db.commit()
            await db.refresh(refund)

    await notify_source_service_refund_status(refund)

    if (
        auto_initiate_paystack
        and refund.status == RefundStatus.APPROVED
        and refund.provider == "paystack"
        and refund.cash_refund_kobo > 0
    ):
        refund = await initiate_paystack_refund(
            db,
            refund,
            actor_auth_id=actor_auth_id,
            customer_note=refund.reason or f"Refund {refund.refund_reference}",
            merchant_note=admin_note or f"{refund.source_service}:{refund.source_entity_id}",
        )

    return refund


async def initiate_paystack_refund(
    db: AsyncSession,
    refund: Refund,
    *,
    actor_auth_id: str,
    customer_note: str | None = None,
    merchant_note: str | None = None,
) -> Refund:
    """Submit an APPROVED Paystack refund to Paystack POST /refund (idempotent)."""
    if refund.status in (RefundStatus.INITIATED, RefundStatus.COMPLETED):
        return refund
    if refund.status not in (RefundStatus.APPROVED, RefundStatus.FAILED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Refund must be approved before Paystack initiation (current: {refund.status.value})",
        )
    if refund.provider != "paystack" or refund.cash_refund_kobo <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="This refund is not eligible for Paystack API refund",
        )
    if not refund.payment_reference:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Missing original payment reference for Paystack refund",
        )

    # Ensure accrual entry is posted first.
    if not refund.ledger_accrual_posted:
        posted = await emit_refund_approved_to_ledger(db, refund)
        if posted:
            refund.ledger_accrual_posted = True
            await db.commit()

    try:
        client = get_paystack_client()
        result = await client.create_refund(
            refund.payment_reference,
            amount_kobo=refund.cash_refund_kobo,
            currency=refund.currency or "NGN",
            customer_note=customer_note or refund.reason,
            merchant_note=merchant_note
            or f"SwimBuddz refund {refund.refund_reference}",
        )
    except (PaystackError, ValueError) as exc:
        refund.failure_reason = str(exc)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack refund initiation failed: {exc}",
        ) from exc

    now = utc_now()
    refund.provider_refund_id = result.refund_id or refund.provider_refund_id
    refund.provider_status = result.status
    refund.provider_payload = result.raw_payload
    refund.initiated_by_auth_id = actor_auth_id
    refund.initiated_at = refund.initiated_at or now
    refund.failure_reason = None

    if result.status == "processed":
        refund.status = RefundStatus.COMPLETED
        refund.completed_by_auth_id = actor_auth_id
        refund.completed_at = now
    elif result.status in ("failed",):
        refund.status = RefundStatus.FAILED
        refund.failed_at = now
        refund.failure_reason = str(
            result.raw_payload.get("message") or "Paystack marked refund failed"
        )
    else:
        refund.status = RefundStatus.INITIATED

    await db.commit()
    await db.refresh(refund)

    if refund.status == RefundStatus.COMPLETED and not refund.ledger_disbursement_posted:
        posted = await emit_refund_completed_to_ledger(db, refund)
        if posted:
            refund.ledger_disbursement_posted = True
            await db.commit()
            await db.refresh(refund)

    await notify_source_service_refund_status(refund)
    return refund


async def apply_paystack_refund_event(
    db: AsyncSession,
    *,
    event: str,
    data: dict,
) -> Optional[Refund]:
    """Reconcile a Paystack refund.* webhook payload against our Refund table."""
    provider_refund_id = str(data.get("id") or "")
    txn_obj = data.get("transaction") or {}
    txn_ref = str(
        data.get("transaction_reference")
        or (txn_obj.get("reference") if isinstance(txn_obj, dict) else "")
        or ""
    )
    amount_kobo = int(data.get("amount") or 0)

    refund: Optional[Refund] = None
    if provider_refund_id:
        refund = (
            await db.execute(
                select(Refund)
                .where(Refund.provider_refund_id == provider_refund_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    if refund is None and txn_ref:
        query = (
            select(Refund)
            .where(
                Refund.payment_reference == txn_ref,
                Refund.provider == "paystack",
                Refund.status.in_((RefundStatus.APPROVED, RefundStatus.INITIATED)),
            )
            .order_by(Refund.created_at.desc())
            .with_for_update()
        )
        candidates = (await db.execute(query)).scalars().all()
        for candidate in candidates:
            if amount_kobo <= 0 or candidate.cash_refund_kobo == amount_kobo:
                refund = candidate
                break

    if refund is None:
        logger.warning(
            "Paystack refund webhook (%s) matched no local Refund (id=%s txn=%s)",
            event,
            provider_refund_id,
            txn_ref,
        )
        return None

    provider_status = str(data.get("status") or event.replace("refund.", "")).lower()
    if provider_refund_id and not refund.provider_refund_id:
        refund.provider_refund_id = provider_refund_id
    refund.provider_status = provider_status
    refund.provider_payload = data

    now = utc_now()
    if event == "refund.processed" or provider_status == "processed":
        if refund.status != RefundStatus.COMPLETED:
            refund.status = RefundStatus.COMPLETED
            refund.completed_at = refund.completed_at or now
            refund.failure_reason = None
    elif event in ("refund.failed", "refund.needs-attention") or provider_status in (
        "failed",
        "needs-attention",
    ):
        if refund.status != RefundStatus.COMPLETED:
            refund.status = RefundStatus.FAILED
            refund.failed_at = now
            refund.failure_reason = str(
                data.get("merchant_note")
                or data.get("status")
                or f"Paystack webhook event: {event}"
            )
    elif event in ("refund.pending", "refund.processing"):
        if refund.status == RefundStatus.APPROVED:
            refund.status = RefundStatus.INITIATED
            refund.initiated_at = refund.initiated_at or now

    await db.commit()
    await db.refresh(refund)

    if refund.status == RefundStatus.COMPLETED and not refund.ledger_disbursement_posted:
        if not refund.ledger_accrual_posted:
            accrued = await emit_refund_approved_to_ledger(db, refund)
            if accrued:
                refund.ledger_accrual_posted = True
        posted = await emit_refund_completed_to_ledger(db, refund)
        if posted:
            refund.ledger_disbursement_posted = True
        await db.commit()
        await db.refresh(refund)

    await notify_source_service_refund_status(refund)
    return refund


async def sync_paystack_refund_status(
    db: AsyncSession,
    refund: Refund,
) -> Refund:
    """Poll Paystack GET /refund/{id} and reconcile local Refund state."""
    if not refund.provider_refund_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Refund has no Paystack refund ID yet",
        )
    try:
        client = get_paystack_client()
        result = await client.get_refund(refund.provider_refund_id)
    except (PaystackError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch Paystack refund status: {exc}",
        ) from exc

    event_map = {
        "processed": "refund.processed",
        "failed": "refund.failed",
        "needs-attention": "refund.needs-attention",
        "processing": "refund.processing",
        "pending": "refund.pending",
    }
    updated = await apply_paystack_refund_event(
        db,
        event=event_map.get(result.status, f"refund.{result.status}"),
        data={
            **result.raw_payload,
            "id": result.refund_id,
            "status": result.status,
            "transaction_reference": result.transaction_reference,
            "amount": result.amount_kobo,
        },
    )
    return updated or refund


async def complete_manual_refund(
    db: AsyncSession,
    refund: Refund,
    *,
    actor_auth_id: str,
    external_bank_reference: str,
    admin_note: str | None = None,
) -> Refund:
    """Confirm manual bank-transfer refund completion with a verified bank reference."""
    cleaned_ref = external_bank_reference.strip()
    if len(cleaned_ref) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A verified bank transaction reference is required",
        )
    if refund.status == RefundStatus.COMPLETED:
        if refund.external_bank_reference == cleaned_ref:
            return refund
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Refund is already completed with a different bank reference",
        )
    if refund.status not in (
        RefundStatus.APPROVED,
        RefundStatus.INITIATED,
        RefundStatus.FAILED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approve refund before marking completed (current: {refund.status.value})",
        )

    now = utc_now()
    refund.provider = "manual_transfer"
    refund.external_bank_reference = cleaned_ref
    if admin_note:
        refund.admin_note = admin_note
    refund.status = RefundStatus.COMPLETED
    refund.completed_by_auth_id = actor_auth_id
    refund.completed_at = now
    refund.failure_reason = None
    await db.commit()
    await db.refresh(refund)

    if not refund.ledger_accrual_posted:
        accrued = await emit_refund_approved_to_ledger(db, refund)
        if accrued:
            refund.ledger_accrual_posted = True
    if not refund.ledger_disbursement_posted:
        posted = await emit_refund_completed_to_ledger(db, refund)
        if posted:
            refund.ledger_disbursement_posted = True
    await db.commit()
    await db.refresh(refund)

    await notify_source_service_refund_status(refund)
    return refund
