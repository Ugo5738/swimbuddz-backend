"""A late failure must never overwrite a concurrently recorded cash receipt."""

from services.payments_service.models import PaymentStatus
from services.payments_service.services.booking_payment_attempts import (
    booking_identity,
    lock_booking_payment,
)


async def record_provider_failure(db, payment, payload) -> bool:
    booking_id = booking_identity(payment)
    if booking_id:
        await lock_booking_payment(db, booking_id)
    await db.refresh(payment, with_for_update=True)
    if payment.status == PaymentStatus.PAID:
        return False
    payment.status = PaymentStatus.FAILED
    payment.provider, payment.provider_reference = "paystack", payment.reference
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "provider_payload": payload,
    }
    await db.commit()
    return True
