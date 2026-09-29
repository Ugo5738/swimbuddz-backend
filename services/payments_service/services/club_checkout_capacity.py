"""Protect quarterly seats before exposing any way to transfer money."""

from fastapi import HTTPException

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post
from services.payments_service.models import PaymentPurpose


async def protect_club_checkout(db, payment):
    meta = payment.payment_metadata or {}
    application_id = meta.get("club_application_id")
    if payment.purpose != PaymentPurpose.CLUB or not application_id:
        return
    reservation = meta.get("club_capacity_reservation") or {}
    if reservation.get("protected_at"):
        return
    body = {
        "payment_reference": payment.reference,
        "payment_mode": meta.get("club_payment_mode") or "quarterly_prepaid",
        "community_experience_selected": bool(
            meta.get("community_experience_selected")
        ),
        "community_experience_fee_kobo": int(
            (meta.get("components_kobo") or {}).get("community_experience") or 0
        ),
    }
    # Legacy pending intents have only a plan-level hold. Acquire the exact
    # session holds before returning their old checkout URL to a member.
    paths = (
        ["/protect"]
        if reservation.get("session_holds")
        or body["payment_mode"] == "transition_per_session"
        else ["", "/protect"]
    )
    for suffix in paths:
        response = await internal_post(
            service_url=get_settings().MEMBERS_SERVICE_URL,
            path=f"/clubs/internal/applications/{application_id}/reservation{suffix}",
            calling_service="payments",
            json=body,
            timeout=120,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            raise HTTPException(
                409 if response.status_code == 409 else 503,
                detail
                or "Could not protect the included Club swim seats. Payment has not been started.",
            )
    payment.payment_metadata = {
        **meta,
        "club_capacity_reservation": {
            **reservation,
            **response.json(),
            "protected_at": utc_now().isoformat(),
        },
    }
    await db.commit()
