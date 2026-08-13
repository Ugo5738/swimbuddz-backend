"""Apply entitlement for PaymentPurpose.CLUB payments.

Extracted from the single-function `_apply_entitlement` dispatcher per
docs/CONVENTIONS.md §12. Each handler owns its own cross-service
contract end-to-end; the dispatcher (`_dispatcher._apply_entitlement`)
just routes by `payment.purpose`.
"""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from services.payments_service.models import (
    Payment,
)

settings = get_settings()


async def apply_club(payment: Payment) -> None:
    months = int((payment.payment_metadata or {}).get("months") or 1)
    application_id = (payment.payment_metadata or {}).get("club_application_id")
    community_extension_months = int(
        (payment.payment_metadata or {}).get("community_extension_months") or 0
    )

    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        # If community extension was included, extend Community first
        if community_extension_months > 0:
            community_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/community/extend",
                json={
                    "months": community_extension_months,
                    "idempotency_key": f"payment:{payment.id}:community-extend",
                    "source_reference": payment.reference,
                },
                headers=headers,
            )
            if community_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Failed to apply bundled community extension via "
                        f"members_service ({community_resp.status_code}): "
                        f"{community_resp.text}"
                    ),
                )

        # Activate Club
        club_resp = await client.post(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{payment.member_auth_id}/club/activate",
            json={
                "months": months,
                "idempotency_key": f"payment:{payment.id}:club-activate",
                "source_reference": payment.reference,
                # New location plans charge annual Community separately. The
                # legacy checkout retains its existing one-year extension.
                "extend_community_membership": not bool(application_id),
            },
            headers=headers,
        )
        if club_resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to apply club entitlement via members_service ({club_resp.status_code}): {club_resp.text}",
            )

        if application_id:
            enrollment_resp = await client.post(
                f"{settings.MEMBERS_SERVICE_URL}/clubs/internal/applications/{application_id}/activate",
                json={
                    "payment_reference": payment.reference,
                    "starts_at": (
                        payment.paid_at.isoformat() if payment.paid_at else None
                    ),
                    "months": months,
                    "community_experience_selected": bool(
                        (payment.payment_metadata or {}).get(
                            "community_experience_selected"
                        )
                    ),
                    "community_experience_fee_kobo": int(
                        (
                            (payment.payment_metadata or {}).get("components_kobo")
                            or {}
                        ).get("community_experience")
                        or 0
                    ),
                },
                headers=headers,
            )
            if enrollment_resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        "Club access was paid but location enrollment could not be "
                        f"applied ({enrollment_resp.status_code}): {enrollment_resp.text}"
                    ),
                )
