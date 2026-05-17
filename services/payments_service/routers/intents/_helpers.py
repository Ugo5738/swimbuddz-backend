"""Generic helpers used across the entitlement flow: notification
dispatch, membership reward events, member email lookup, fulfillment
metadata bookkeeping, retry-time computation, club-price resolution,
and best-effort referral qualification.

Tests patch `internal_post` and `logger` on this module directly when
exercising `_try_qualify_referral`.
"""

from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
    internal_post,
)
from libs.common.datetime_utils import utc_now
from services.payments_service.models import (
    Payment,
    PaymentPurpose,
)
from services.payments_service.schemas import (
    ClubBillingCycle,
    CreatePaymentIntentRequest,
    SessionAttendanceStatus,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2


def _require_attendance_status(
    raw_status: SessionAttendanceStatus | str | None, source: str
) -> SessionAttendanceStatus:
    if isinstance(raw_status, SessionAttendanceStatus):
        return raw_status
    status_value = str(raw_status or "").strip()
    if not status_value:
        return SessionAttendanceStatus.PRESENT
    try:
        return SessionAttendanceStatus(status_value)
    except ValueError:
        allowed = ", ".join([status.value for status in SessionAttendanceStatus])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{source} must be one of: {allowed}",
        )


async def _update_pending_payment_reference(
    auth_id: str, reference: str | None
) -> None:
    """Update or clear the pending_payment_reference on a member's membership."""
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            f"{settings.MEMBERS_SERVICE_URL}/admin/members/by-auth/{auth_id}/membership",
            json={"pending_payment_reference": reference},
            headers=headers,
        )
        # Ignore failures - this is a best-effort feature
        if resp.status_code >= 400:
            logger.warning(
                f"Failed to update pending_payment_reference for {auth_id}: {resp.status_code}"
            )


def _next_retry_time(attempts: int) -> datetime:
    # Exponential backoff capped at 60 minutes.
    delay = min(60, BASE_FULFILLMENT_RETRY_MINUTES * (2 ** max(attempts - 1, 0)))
    return utc_now() + timedelta(minutes=delay)


def _fulfillment_meta(payment: Payment) -> dict:
    metadata = payment.payment_metadata or {}
    return dict(metadata.get(FULFILLMENT_META_KEY) or {})


def _set_fulfillment_meta(payment: Payment, **fields) -> None:
    metadata = dict(payment.payment_metadata or {})
    fulfillment = _fulfillment_meta(payment)
    fulfillment.update(fields)
    metadata[FULFILLMENT_META_KEY] = fulfillment
    payment.payment_metadata = metadata


async def _try_qualify_referral(member_auth_id: str, payment_reference: str) -> None:
    """Best-effort: notify wallet service to qualify referral after membership payment."""
    try:
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/referral-qualify",
            calling_service="payments",
            json={
                "member_auth_id": member_auth_id,
                "trigger": f"membership_payment:{payment_reference}",
            },
        )
        if resp.status_code >= 400:
            logger.warning(
                "Referral qualification HTTP failure for %s after payment %s (status=%d): %s",
                member_auth_id,
                payment_reference,
                resp.status_code,
                resp.text,
            )
            return

        body = resp.json() if resp.content else {}
        if body.get("qualified"):
            logger.info(
                "Referral qualified for %s after payment %s",
                member_auth_id,
                payment_reference,
            )
    except Exception as e:
        # Never fail the payment flow for referral issues
        logger.warning(
            "Referral qualification call failed for %s: %s",
            member_auth_id,
            e,
        )


async def _send_tier_activated_email(
    payment: Payment, tier: str, duration: str
) -> None:
    """Best-effort tier activation email after successful payment."""
    try:
        from libs.common.service_client import get_member_by_auth_id

        member = await get_member_by_auth_id(
            payment.member_auth_id, calling_service="payments"
        )
        member_email = (member or {}).get("email") or payment.payer_email
        member_name = (member or {}).get("first_name") or "there"

        if member_email:
            email_client = get_email_client()
            await email_client.send_template(
                template_type="tier_activated",
                to_email=member_email,
                template_data={
                    "member_name": member_name,
                    "tier": tier,
                    "amount": float(payment.amount),
                    "currency": payment.currency,
                    "duration": duration,
                },
            )
            logger.info(
                "Tier activation email sent for %s tier to %s", tier, member_email
            )
    except Exception as e:
        # Non-fatal — payment was successful; email failure must not raise
        logger.warning("Failed to send tier activation email (non-fatal): %s", e)


async def _dispatch_payment_notification(payment: Payment) -> None:
    """Best-effort: send in-app notification after successful payment."""
    try:
        member = await get_member_by_auth_id(
            payment.member_auth_id, calling_service="payments"
        )
        if not member:
            return

        purpose_labels = {
            PaymentPurpose.COMMUNITY: (
                "Community Membership Activated",
                "community",
                "users",
            ),
            PaymentPurpose.CLUB: ("Club Membership Activated", "club", "users"),
            PaymentPurpose.CLUB_BUNDLE: ("Club Membership Activated", "club", "users"),
            PaymentPurpose.ACADEMY_COHORT: (
                "Academy Enrollment Payment",
                "academy",
                "graduation-cap",
            ),
            PaymentPurpose.SESSION_FEE: ("Session Fee Paid", "sessions", "calendar"),
            PaymentPurpose.SESSION_BUNDLE: (
                "Session Bundle Paid",
                "sessions",
                "calendar",
            ),
            PaymentPurpose.RIDE_SHARE: ("Ride Share Payment", "transport", "car"),
            PaymentPurpose.STORE_ORDER: (
                "Store Order Payment",
                "store",
                "shopping-bag",
            ),
            PaymentPurpose.WALLET_TOPUP: (
                "Wallet Top-Up Confirmed",
                "payments",
                "wallet",
            ),
        }

        label = purpose_labels.get(payment.purpose)
        if not label:
            return

        title, category, icon = label
        amount_str = f"₦{float(payment.amount):,.0f}"

        await dispatch_notification(
            type="payment_confirmed",
            category=category,
            member_ids=[str(member["id"])],
            title=title,
            body=f"Payment of {amount_str} confirmed. Reference: {payment.reference}",
            action_url="/account/billing",
            icon=icon,
            metadata={
                "payment_id": str(payment.id),
                "reference": payment.reference,
                "amount": float(payment.amount),
                "purpose": payment.purpose.value,
            },
            calling_service="payments",
        )
    except Exception as e:
        logger.warning("Failed to dispatch payment notification (non-fatal): %s", e)


async def _emit_membership_reward_events(payment: Payment) -> None:
    """Best-effort: emit reward events for membership-related payments.

    Maps payment purposes to reward event types:
    - COMMUNITY → membership.activated (first paid activation) or membership.renewed
    - CLUB/CLUB_BUNDLE → membership.upgraded
    - ACADEMY_COHORT → handled separately by academy graduation
    - SESSION_FEE, STORE_ORDER, WALLET_TOPUP → handled by their respective services
    """
    payment_metadata = payment.payment_metadata or {}
    community_event_type = payment_metadata.get(
        "community_reward_event_type", "membership.renewed"
    )
    purpose_to_event = {
        PaymentPurpose.COMMUNITY: (community_event_type, "community"),
        PaymentPurpose.CLUB: ("membership.upgraded", "club"),
        PaymentPurpose.CLUB_BUNDLE: ("membership.upgraded", "club"),
    }

    mapping = purpose_to_event.get(payment.purpose)
    if not mapping:
        return

    event_type, new_tier = mapping
    try:
        await emit_rewards_event(
            event_type=event_type,
            member_auth_id=payment.member_auth_id,
            service_source="payments",
            event_data={
                "new_tier": new_tier,
                "payment_reference": payment.reference,
                "amount_ngn": float(payment.amount),
                "membership_event_type": event_type,
            },
            idempotency_key=f"membership-{payment.reference}",
            calling_service="payments",
        )
    except Exception:
        logger.warning(
            "Failed to emit membership reward event for %s (best-effort)",
            payment.reference,
            exc_info=True,
        )


def _resolve_club_amount(
    payload: CreatePaymentIntentRequest,
) -> tuple[float, int, ClubBillingCycle]:
    # Default to quarterly (monthly removed)
    cycle = payload.club_billing_cycle or ClubBillingCycle.QUARTERLY
    if cycle == ClubBillingCycle.ANNUAL:
        amount = float(getattr(settings, "CLUB_ANNUAL_FEE_NGN", 150000))
        months = 12
    elif cycle == ClubBillingCycle.BIANNUAL:
        amount = float(getattr(settings, "CLUB_BIANNUAL_FEE_NGN", 80000))
        months = 6
    else:  # QUARTERLY (default)
        amount = float(getattr(settings, "CLUB_QUARTERLY_FEE_NGN", 42500))
        months = 3
    return amount, months, cycle
