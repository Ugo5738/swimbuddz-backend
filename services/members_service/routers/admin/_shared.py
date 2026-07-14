"""Shared helpers for the admin members routers."""

from fastapi import HTTPException, status
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import Member, MemberEntitlementApplication

logger = get_logger(__name__)
settings = get_settings()


async def _claim_entitlement_application(
    db: AsyncSession,
    *,
    member: Member,
    idempotency_key: str | None,
    tier: str,
    action: str,
    source_reference: str | None,
) -> bool:
    """Return True for a new mutation and False for an identical replay."""
    if not idempotency_key:
        return True

    result = await db.execute(
        select(MemberEntitlementApplication).where(
            MemberEntitlementApplication.idempotency_key == idempotency_key
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if (
            existing.member_id != member.id
            or existing.tier != tier
            or existing.action != action
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used for another entitlement",
            )
        return False

    db.add(
        MemberEntitlementApplication(
            member_id=member.id,
            idempotency_key=idempotency_key,
            tier=tier,
            action=action,
            source_reference=source_reference,
        )
    )
    return True


async def _apply_wallet_paid_activation_side_effects(
    member: Member,
    *,
    first_paid_community_activation: bool,
) -> None:
    """Ensure wallet exists and apply role-based welcome bonus eligibility."""
    roles = {
        role.strip().lower()
        for role in (member.roles or [])
        if isinstance(role, str) and role.strip()
    }
    is_coach = "coach" in roles
    eligible_for_bonus = (
        first_paid_community_activation
        and ("member" in roles or "coach" in roles)
        and (settings.WELCOME_BONUS_INCLUDE_COACHES or not is_coach)
    )

    try:
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/welcome-bonus",
            calling_service="members",
            json={
                "member_id": str(member.id),
                "member_auth_id": member.auth_id,
                "eligible": eligible_for_bonus,
                "reason": "Welcome bonus after paid community activation",
                "granted_by": "members_service",
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Wallet paid-activation side effect failed for %s (http %d): %s",
                member.auth_id,
                resp.status_code,
                resp.text,
            )
    except Exception as exc:
        logger.warning(
            "Wallet paid-activation side effect request failed for %s: %s",
            member.auth_id,
            exc,
        )
