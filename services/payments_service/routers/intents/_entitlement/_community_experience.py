"""Apply a paid quarter-specific Community Experience entitlement."""

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from services.payments_service.models import Payment

settings = get_settings()


async def apply_community_experience(payment: Payment) -> None:
    metadata = payment.payment_metadata or {}
    offering_id = metadata.get("community_experience_offering_id")
    if not offering_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Community Experience payment is missing its offering",
        )
    membership_months = int(metadata.get("community_extension_months") or 0)
    components = metadata.get("components_kobo") or {}
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        if membership_months > 0:
            membership_response = await client.post(
                (
                    f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/"
                    f"{payment.member_auth_id}/community/extend"
                ),
                json={
                    "months": membership_months,
                    "idempotency_key": f"payment:{payment.id}:community-extend",
                    "source_reference": payment.reference,
                },
                headers=headers,
            )
            if membership_response.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Could not activate the bundled annual SwimBuddz Membership",
                )
        response = await client.post(
            (
                f"{settings.MEMBERS_SERVICE_URL}/clubs/community-experiences/"
                f"internal/{offering_id}/activate"
            ),
            json={
                "member_auth_id": payment.member_auth_id,
                "payment_reference": payment.reference,
                "amount_paid_kobo": int(components.get("community_experience") or 0),
                "price_context": metadata.get("community_experience_price_context")
                or "standard_member",
            },
            headers=headers,
        )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Community Experience payment succeeded but access could not be activated",
            )
