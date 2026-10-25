"""Admin-only combined payment links for recorded session charges."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.routers.intents._paystack import (
    _initialize_paystack,
    _paystack_enabled,
)
from services.payments_service.schemas.session_settlement import (
    AdminSessionSettlementRequest,
    AdminSessionSettlementResponse,
)
from services.payments_service.services.booking_payment_attempts import (
    booking_payments,
    blocks_new_attempt,
)
from services.payments_service.services.manual_transfer import lock_external_reference
from services.payments_service.services.session_settlement import (
    METADATA_KEY,
    initialize_checkout,
    lock_subjects,
    sessions_request,
)

router = APIRouter(prefix="/payments/admin", tags=["session-settlements"])


@router.post("/session-settlements", response_model=AdminSessionSettlementResponse)
async def create_session_settlement(
    payload: AdminSessionSettlementRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    if not _paystack_enabled():
        raise HTTPException(503, "Online payments are unavailable")
    reference = f"PAY-SETTLE-{payload.idempotency_key.hex}"
    await lock_external_reference(db, f"session-settlement:{reference}")
    await lock_subjects(db, payload.booking_ids, payload.guest_participant_ids)
    payment = (
        await db.execute(select(Payment).where(Payment.reference == reference))
    ).scalar_one_or_none()
    if payment:
        meta = payment.payment_metadata or {}
        frozen = meta.get(METADATA_KEY) or {}
        if (
            payment.member_auth_id != payload.member_auth_id
            or set(meta.get("booking_ids", [])) != {str(x) for x in payload.booking_ids}
            or set(frozen.get("guest_participant_ids", []))
            != {str(x) for x in payload.guest_participant_ids}
            or frozen.get("total_kobo") != payload.expected_total_kobo
        ):
            raise HTTPException(
                409, "This payment key belongs to different session charges"
            )
    else:
        for booking_id in payload.booking_ids:
            if any(
                blocks_new_attempt(row)
                for row in await booking_payments(db, booking_id)
            ):
                raise HTTPException(
                    409,
                    "A selected booking already has a payment; reconcile it before issuing a combined link",
                )
        for guest_id in payload.guest_participant_ids:
            guest_attempts = list(
                (
                    await db.execute(
                        select(Payment).where(
                            or_(
                                and_(
                                    Payment.purpose == PaymentPurpose.SESSION_BUNDLE,
                                    Payment.payment_metadata[METADATA_KEY][
                                        "guest_participant_ids"
                                    ].contains([str(guest_id)]),
                                ),
                                and_(
                                    Payment.purpose == PaymentPurpose.GUEST_PASS,
                                    Payment.payment_metadata[
                                        "session_participant_id"
                                    ].astext
                                    == str(guest_id),
                                ),
                            ),
                        )
                    )
                ).scalars()
            )
            if any(blocks_new_attempt(row) for row in guest_attempts):
                raise HTTPException(
                    409, "A selected guest already has a combined payment"
                )
        quote = await sessions_request(
            "quote",
            {
                "member_auth_id": payload.member_auth_id,
                "booking_ids": [str(x) for x in payload.booking_ids],
                "guest_participant_ids": [
                    str(x) for x in payload.guest_participant_ids
                ],
            },
        )
        if quote["total_kobo"] != payload.expected_total_kobo:
            raise HTTPException(
                409, "The recorded charges do not match the expected total"
            )
        member = await get_member_by_auth_id(
            payload.member_auth_id, calling_service="payments"
        )
        if not member or not member.get("email"):
            raise HTTPException(
                422, "The payer needs a member account with an email address"
            )
        member_name = (
            " ".join(
                (member.get(key) or "").strip() for key in ("first_name", "last_name")
            ).strip()
            or "Member"
        )
        for line in quote["lines"]:
            if line["kind"] == "booking":
                line["name"] = member_name
        payment = Payment(
            id=uuid.uuid4(),
            reference=reference,
            member_auth_id=payload.member_auth_id,
            payer_email=member["email"],
            purpose=PaymentPurpose.SESSION_BUNDLE,
            amount=quote["total_kobo"] / 100,
            currency="NGN",
            status=PaymentStatus.PENDING,
            payment_method="paystack",
            admin_review_note=payload.note,
            payment_metadata={
                "session_type": quote["session_type"],
                "booking_ids": [str(x) for x in payload.booking_ids],
                "session_ids": list(
                    dict.fromkeys(line["session_id"] for line in quote["lines"])
                ),
                "session_count": len({line["session_id"] for line in quote["lines"]}),
                METADATA_KEY: {
                    "version": 1,
                    "total_kobo": quote["total_kobo"],
                    "lines": quote["lines"],
                    "guest_participant_ids": [
                        str(x) for x in payload.guest_participant_ids
                    ],
                    "created_by": admin.user_id,
                    "note": payload.note,
                },
            },
        )
        db.add(payment)
        await db.commit()
    url = await initialize_checkout(db, payment, _initialize_paystack)
    return {
        "reference": payment.reference,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status.value,
        "authorization_url": url,
        "lines": payment.payment_metadata[METADATA_KEY]["lines"],
    }
