"""Apply entitlement for PaymentPurpose.ACADEMY_COHORT payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.logging import get_logger
from libs.common.emails.client import get_email_client
from libs.common.datetime_utils import utc_now
from services.payments_service.models import (
    Payment,
)

settings = get_settings()
logger = get_logger(__name__)


async def apply_academy_cohort(payment: Payment) -> None:
    metadata = payment.payment_metadata or {}
    enrollment_id = metadata.get("enrollment_id")
    if not enrollment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enrollment_id missing in payment metadata",
        )
    installment_id = metadata.get("installment_id")
    installment_number = metadata.get("installment_number")
    total_installments = metadata.get("total_installments")
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    mark_paid_payload = {
        "payment_reference": payment.reference,
        "paid_at": (
            payment.paid_at.isoformat() if payment.paid_at else utc_now().isoformat()
        ),
        # Pass the actual amount paid (kobo). When this exceeds the target
        # installment's stipulated amount (member chose a custom amount),
        # the academy mark-paid endpoint rolls forward across installments.
        "amount_kobo": int(
            metadata.get("academy_payment_amount_kobo")
            or round((payment.amount or 0) * KOBO_PER_NAIRA)
        ),
    }
    if payment.amount <= 0:
        # Fully discounted enrollment should not retain installment obligations.
        mark_paid_payload["clear_installments"] = True
    if installment_id:
        mark_paid_payload["installment_id"] = installment_id
    if installment_number:
        mark_paid_payload["installment_number"] = installment_number
    async with httpx.AsyncClient(timeout=30) as client:
        membership_months = int(metadata.get("community_extension_months") or 0)
        if membership_months > 0:
            membership_response = await client.post(
                (
                    f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/"
                    f"{payment.member_auth_id}/community/extend"
                ),
                headers=headers,
                json={
                    "months": membership_months,
                    "idempotency_key": f"payment:{payment.id}:community-extend",
                    "source_reference": payment.reference,
                },
            )
            if membership_response.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Could not activate the Academy membership entitlement",
                )
        resp = await client.post(
            f"{settings.ACADEMY_SERVICE_URL}/academy/admin/enrollments/{enrollment_id}/mark-paid",
            headers=headers,
            json=mark_paid_payload,
        )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to mark enrollment as paid ({resp.status_code}): {resp.text}",
            )

        # academy_service owns enrollment truth and only returns success after
        # applying the idempotent members_service entitlement. A non-2xx above
        # leaves this payment fulfillment retryable.

    # Send subsequent installment payment confirmation (not for first installment —
    # first installment confirmation is sent by the academy service's mark-paid endpoint).
    if installment_number and int(installment_number) > 1:
        try:
            member_headers = {
                "Authorization": f"Bearer {_service_role_jwt('payments')}"
            }
            async with httpx.AsyncClient(timeout=30) as client:
                member_resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{payment.member_auth_id}",
                    headers=member_headers,
                )
                if member_resp.status_code < 400:
                    member_data = member_resp.json()
                    member_email = member_data.get("email") or payment.payer_email
                    member_name = member_data.get("first_name", "Student")
                else:
                    member_email = payment.payer_email
                    member_name = "Student"

            if member_email:
                email_client = get_email_client()
                await email_client.send_template(
                    template_type="installment_payment_confirmation",
                    to_email=member_email,
                    template_data={
                        "member_name": member_name,
                        "installment_number": int(installment_number),
                        "total_installments": (
                            int(total_installments) if total_installments else None
                        ),
                        "amount": payment.amount,
                        "currency": payment.currency,
                        "payment_reference": payment.reference,
                        "paid_at": (
                            payment.paid_at.strftime("%B %d, %Y")
                            if payment.paid_at
                            else utc_now().strftime("%B %d, %Y")
                        ),
                    },
                )
                logger.info(
                    f"Sent installment payment confirmation to {member_email} "
                    f"(installment {installment_number} of {total_installments})"
                )
        except Exception as e:
            # Non-fatal — payment was successful; email failure must not raise
            logger.error(
                f"Failed to send installment payment confirmation for {payment.reference}: {e}"
            )
