"""Initialize/resume a frozen guest payment, including manual transfers."""

import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from services.sessions_service.models import GuestPass, Session
from services.sessions_service.services.booking_confirmation import (
    deliver_confirmation,
    queue_confirmation,
)
from services.sessions_service.services.guest_booking import public_receipt, receipt_url


async def start_checkout(db: AsyncSession, guest_pass_id: uuid.UUID) -> dict:
    guest = (
        await db.execute(
            select(GuestPass).where(GuestPass.id == guest_pass_id).with_for_update()
        )
    ).scalar_one()
    session = await db.get(Session, guest.session_id)
    if session.status in {"draft", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail="This session is no longer available. Contact SwimBuddz.",
        )
    if guest.status in {"confirmed", "attended"}:
        result = await public_receipt(guest, session, private=True)
        await db.commit()
        return {**result, "receipt_url": receipt_url(guest.id)}
    if guest.status not in {"pending_payment", "payment_failed"}:
        raise HTTPException(status_code=409, detail="This guest booking cannot be paid")
    if guest.booking_mode == "reservation" and (
        not guest.reservation_expires_at or guest.reservation_expires_at <= utc_now()
    ):
        # A retry must not renew a scarce-seat hold indefinitely. Admin may
        # reconcile an already-attended swim, but payment is never attendance.
        raise HTTPException(
            status_code=409,
            detail="The reservation has expired. Contact SwimBuddz to restore your booking before paying.",
        )
    if guest.price_kobo == 0:
        guest.status = "confirmed"
        guest.reservation_expires_at = None
        key = await queue_confirmation(db, guest.id, guest=True)
        await db.commit()
        await deliver_confirmation(db, key)
    else:
        try:
            response = await internal_post(
                service_url=get_settings().PAYMENTS_SERVICE_URL,
                path="/internal/payments/initialize",
                calling_service="sessions",
                json={
                    "purpose": "guest_pass",
                    "payment_method": guest.payment_method,
                    "amount": guest.price_kobo / 100,
                    "currency": "NGN",
                    "reference": guest.payment_reference,
                    "member_auth_id": f"guest:{guest.id}",
                    "callback_url": receipt_url(guest.id),
                    "metadata": {
                        "guest_pass_id": str(guest.id),
                        "session_id": str(session.id),
                        "reservation_expires_at": guest.reservation_expires_at.isoformat()
                        if guest.reservation_expires_at
                        else None,
                        "booking_mode": guest.booking_mode,
                        "payer_email": guest.email,
                        "referral_code": guest.referral_code,
                        "booking_source": guest.booking_source,
                        "campaign_key": guest.campaign_key,
                    },
                },
                timeout=30,
            )
            response.raise_for_status()
            checkout = response.json()
            guest.additional_charges = checkout.get("additional_charges") or []
            guest.total_kobo = int(checkout.get("amount_kobo") or guest.price_kobo)
            guest.status = "pending_payment"
            await db.commit()
            return {
                **await public_receipt(guest, session, private=True),
                "checkout_url": checkout.get("authorization_url"),
                "receipt_url": receipt_url(guest.id),
            }
        except (httpx.HTTPError, ValueError, KeyError):
            guest.status = "payment_failed"
            await db.commit()
            # Return the private receipt even when the provider is down. It
            # carries the capability needed to safely retry this same payment.
    return {
        **await public_receipt(guest, session, private=True),
        "receipt_url": receipt_url(guest.id),
    }
