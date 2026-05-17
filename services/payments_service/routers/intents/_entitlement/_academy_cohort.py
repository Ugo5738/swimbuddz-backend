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

from .._helpers import (
    _update_pending_payment_reference,
)

settings = get_settings()
logger = get_logger(__name__)


async def apply_academy_cohort(payment: Payment) -> None:
    enrollment_id = (payment.payment_metadata or {}).get("enrollment_id")
    if not enrollment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="enrollment_id missing in payment metadata",
        )
    installment_id = (payment.payment_metadata or {}).get("installment_id")
    installment_number = (payment.payment_metadata or {}).get("installment_number")
    total_installments = (payment.payment_metadata or {}).get("total_installments")
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    mark_paid_payload = {
        "payment_reference": payment.reference,
        "paid_at": (
            payment.paid_at.isoformat() if payment.paid_at else utc_now().isoformat()
        ),
        # Pass the actual amount paid (kobo). When this exceeds the target
        # installment's stipulated amount (member chose a custom amount),
        # the academy mark-paid endpoint rolls forward across installments.
        "amount_kobo": int(round((payment.amount or 0) * KOBO_PER_NAIRA)),
    }
    if payment.amount <= 0:
        # Fully discounted enrollment should not retain installment obligations.
        mark_paid_payload["clear_installments"] = True
    if installment_id:
        mark_paid_payload["installment_id"] = installment_id
    if installment_number:
        mark_paid_payload["installment_number"] = installment_number
    async with httpx.AsyncClient(timeout=30) as client:
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

        # Activate the academy tier on the member using the cohort end_date from
        # the mark-paid response. We always pass the latest cohort end date so
        # members with multiple simultaneous enrollments keep access until their
        # last cohort finishes (the activate endpoint keeps the later date).
        try:
            enrollment_data = resp.json()
            cohort_end_date = (enrollment_data.get("cohort") or {}).get(
                "end_date"
            ) or enrollment_data.get("cohort_end_date")
            if cohort_end_date and payment.member_auth_id:
                academy_resp = await client.post(
                    f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/academy/activate",
                    headers=headers,
                    json={"cohort_end_date": cohort_end_date},
                )
                if academy_resp.status_code >= 400:
                    logger.warning(
                        f"Failed to activate academy tier for {payment.member_auth_id}: "
                        f"{academy_resp.status_code} {academy_resp.text}"
                    )
            else:
                logger.warning(
                    f"Academy cohort end_date missing in mark-paid response for "
                    f"enrollment {enrollment_id} — academy tier not activated"
                )
        except Exception as e:
            # Non-fatal: enrollment is paid; tier activation failure is logged
            logger.error(
                f"Failed to activate academy tier after payment {payment.reference}: {e}"
            )

    # Clear pending payment reference on success
    await _update_pending_payment_reference(payment.member_auth_id, None)

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
