"""Authenticated reservation details and reception admission verification."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.pools_service.models.access import (
    PoolAccessAdmission,
    PoolAccessBooking,
    PoolAccessOffer,
    PoolAccessReconciliation,
)
from services.pools_service.services.access_policy import (
    admission_id_from_ticket,
    reconciled_cost,
    ticket_for,
)

router = APIRouter(tags=["pool-access-redemption"])


@router.get("/bookings/{booking_id}/tickets")
async def my_tickets(
    booking_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    booking = await db.get(PoolAccessBooking, booking_id)
    if not booking or booking.buyer_auth_id != user.user_id:
        raise HTTPException(404, "Booking not found")
    if booking.status != "confirmed":
        raise HTTPException(409, "Tickets require confirmed payment")
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if offer.ends_at <= utc_now():
        raise HTTPException(409, "Admission window has ended")
    admissions = (
        (
            await db.execute(
                select(PoolAccessAdmission)
                .where(PoolAccessAdmission.booking_id == booking_id)
                .order_by(PoolAccessAdmission.ordinal)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(admission.id),
            "guest_name": admission.guest_name,
            "ticket": ticket_for(admission.id)
            if admission.checked_in_at is None
            else None,
            "checked_in_at": admission.checked_in_at,
        }
        for admission in admissions
    ]


@router.post("/redeem")
async def redeem(
    ticket: str,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin-only check-in for first pilot. Partner-scoped roles required before rollout."""
    admission_id = admission_id_from_ticket(ticket)
    admission = (
        await db.execute(
            select(PoolAccessAdmission)
            .where(PoolAccessAdmission.id == admission_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not admission:
        raise HTTPException(404, "Admission not found")
    booking = await db.get(PoolAccessBooking, admission.booking_id)
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    now = utc_now()
    if booking.status != "confirmed" or not booking.payment_reference:
        raise HTTPException(409, "Booking is not paid")
    if now < offer.starts_at or now > offer.ends_at:
        raise HTTPException(409, "Outside permitted admission window")
    if admission.checked_in_at:
        raise HTTPException(409, "Ticket already redeemed")
    admission.checked_in_at = now
    admission.checked_in_by = user.user_id
    await db.commit()
    return {
        "status": "admitted",
        "admission_id": str(admission.id),
        "guest_name": admission.guest_name,
        "pool_id": str(offer.pool_id),
    }


@router.post("/bookings/{booking_id}/reconcile")
async def reconcile(
    booking_id: uuid.UUID,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    booking = (
        await db.execute(
            select(PoolAccessBooking)
            .where(PoolAccessBooking.id == booking_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not booking or booking.status != "confirmed" or not booking.payment_reference:
        raise HTTPException(409, "Only paid bookings can be reconciled")
    prior = (
        await db.execute(
            select(PoolAccessReconciliation).where(
                PoolAccessReconciliation.booking_id == booking_id
            )
        )
    ).scalar_one_or_none()
    if prior:
        return {
            "booking_id": str(booking_id),
            "verified_admissions": prior.verified_admissions,
            "payable_kobo": prior.payable_kobo,
            "status": "snapshotted",
        }
    offer = await db.get(PoolAccessOffer, booking.offer_id)
    if offer.ends_at > utc_now():
        raise HTTPException(409, "Visit has not finished")
    entries = (
        (
            await db.execute(
                select(PoolAccessAdmission).where(
                    PoolAccessAdmission.booking_id == booking_id,
                    PoolAccessAdmission.checked_in_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    liability = reconciled_cost(
        booking.negotiated_cost_kobo, booking.cost_basis, len(entries)
    )
    statement = PoolAccessReconciliation(
        booking_id=booking.id,
        verified_admissions=len(entries),
        payable_kobo=liability,
        currency=booking.currency,
        payment_reference=booking.payment_reference,
        reconciled_by=user.user_id,
    )
    db.add(statement)
    await db.commit()
    return {
        "booking_id": str(booking_id),
        "verified_admissions": len(entries),
        "payable_kobo": liability,
        "status": "snapshotted",
    }

@router.post("/reconciliations/{reconciliation_id}/record-external-settlement")
async def record_external_settlement(
    reconciliation_id: uuid.UUID,
    payload: dict,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Record bank-confirmed settlement, never execute or claim to execute a transfer."""
    from sqlalchemy import func
    from services.pools_service.models.access import PoolAccessPartnerSettlement

    reference = str(payload.get("bank_reference") or "").strip()
    note = str(payload.get("evidence_note") or "").strip()
    amount = payload.get("amount_kobo")
    if (
        len(reference) < 8
        or len(reference) > 160
        or len(note) < 8
        or len(note) > 2000
        or not isinstance(amount, int)
        or isinstance(amount, bool)
        or amount <= 0
    ):
        raise HTTPException(422, "Bank reference, payment evidence and positive amount are required")
    reconciliation = (
        await db.execute(
            select(PoolAccessReconciliation)
            .where(PoolAccessReconciliation.id == reconciliation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if reconciliation is None:
        raise HTTPException(404, "Partner liability not found")
    prior = (
        await db.execute(
            select(PoolAccessPartnerSettlement).where(
                PoolAccessPartnerSettlement.bank_reference == reference
            )
        )
    ).scalar_one_or_none()
    if prior:
        if (
            prior.reconciliation_id == reconciliation.id
            and prior.amount_kobo == amount
        ):
            return {"status": "already_recorded", "settlement_id": str(prior.id)}
        raise HTTPException(409, "Bank reference already used for another settlement")
    settled = (
        await db.execute(
            select(func.coalesce(func.sum(PoolAccessPartnerSettlement.amount_kobo), 0)).where(
                PoolAccessPartnerSettlement.reconciliation_id == reconciliation.id
            )
        )
    ).scalar_one()
    if amount > reconciliation.payable_kobo - settled:
        raise HTTPException(409, "Settlement exceeds outstanding verified pool liability")
    settlement = PoolAccessPartnerSettlement(
        reconciliation_id=reconciliation.id,
        amount_kobo=amount,
        currency=reconciliation.currency,
        bank_reference=reference,
        evidence_note=note,
        recorded_by=user.user_id,
    )
    db.add(settlement)
    await db.commit()
    return {
        "status": "recorded_external_payment",
        "settlement_id": str(settlement.id),
        "remaining_kobo": reconciliation.payable_kobo - settled - amount,
    }


@router.get("/reconciliations/{reconciliation_id}/settlements")
async def settlement_records(
    reconciliation_id: uuid.UUID,
    user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    from services.pools_service.models.access import PoolAccessPartnerSettlement

    reconciliation = await db.get(PoolAccessReconciliation, reconciliation_id)
    if reconciliation is None:
        raise HTTPException(404, "Partner liability not found")
    rows = (
        await db.execute(
            select(PoolAccessPartnerSettlement)
            .where(PoolAccessPartnerSettlement.reconciliation_id == reconciliation_id)
            .order_by(PoolAccessPartnerSettlement.recorded_at)
        )
    ).scalars().all()
    return {
        "liability_kobo": reconciliation.payable_kobo,
        "settled_kobo": sum(s.amount_kobo for s in rows),
        "currency": reconciliation.currency,
        "entries": [
            {
                "id": str(s.id),
                "bank_reference": s.bank_reference,
                "amount_kobo": s.amount_kobo,
                "recorded_at": s.recorded_at,
            }
            for s in rows
        ],
    }

