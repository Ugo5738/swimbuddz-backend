"""Academy-tier activation and source-of-truth projection endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import (
    ActivateAcademyRequest,
    MemberResponse,
    ProjectAcademyRequest,
)
from services.members_service.services.member_service import (
    normalize_member_tiers,
)

from ._shared import _claim_entitlement_application

logger = get_logger(__name__)
router = APIRouter()


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def _load_member_for_update(
    db: AsyncSession,
    *,
    auth_id: str,
) -> Member:
    result = await db.execute(
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
        .with_for_update()
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    return member


def _normalize_stored_tiers(member: Member) -> None:
    membership = member.membership
    if not membership:
        return
    primary_tier, active_tiers, _ = normalize_member_tiers(
        current_tier=membership.primary_tier,
        current_tiers=membership.active_tiers,
        community_paid_until=membership.community_paid_until,
        club_paid_until=membership.club_paid_until,
        academy_paid_until=membership.academy_paid_until,
        post_academy_club_until=membership.post_academy_club_until,
    )
    membership.primary_tier = primary_tier
    membership.active_tiers = active_tiers


async def _response_member(db: AsyncSession, member_id) -> Member:
    result = await db.execute(
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    return result.scalar_one()


@router.post("/by-auth/{auth_id}/academy/expire", response_model=MemberResponse)
async def admin_expire_academy_membership_by_auth(
    auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Expire Academy access immediately.

    Kept for operational compatibility. Academy's normal lifecycle uses the
    exact ``/academy/project`` endpoint so another active cohort is preserved.
    """
    member = await _load_member_for_update(db, auth_id=auth_id)
    if not member.membership:
        return member

    member.membership.academy_paid_until = utc_now()
    _normalize_stored_tiers(member)
    await db.commit()
    return await _response_member(db, member.id)


@router.post("/by-auth/{auth_id}/academy/activate", response_model=MemberResponse)
async def admin_activate_academy_membership_by_auth(
    auth_id: str,
    payload: ActivateAcademyRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply a paid Academy entitlement once and preserve later cohorts.

    The idempotency key protects the Academy date. Annual Membership is
    extended separately by the payment flow only when the programme's policy
    is ``active_required`` or ``included``; an ``open`` Academy programme must
    not silently grant Membership. Club access is likewise independent, with
    its explicit one-month bridge granted separately on graduation.
    """
    member = await _load_member_for_update(db, auth_id=auth_id)
    should_apply = await _claim_entitlement_application(
        db,
        member=member,
        idempotency_key=payload.idempotency_key,
        tier="academy",
        action="activate",
        source_reference=payload.source_reference,
    )
    if not should_apply:
        await db.commit()
        return await _response_member(db, member.id)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    new_end = _as_aware(payload.cohort_end_date)
    current_until = member.membership.academy_paid_until
    if current_until is None or new_end > current_until:
        member.membership.academy_paid_until = new_end
    member.membership.declared_tiers = sorted(
        set(member.membership.declared_tiers or ["community"]) | {"academy"}
    )
    _normalize_stored_tiers(member)

    await db.commit()
    return await _response_member(db, member.id)


@router.post("/by-auth/{auth_id}/academy/project", response_model=MemberResponse)
async def admin_project_academy_membership_by_auth(
    auth_id: str,
    payload: ProjectAcademyRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Replace Academy access with academy_service's authoritative projection.

    This mutation is naturally idempotent and intentionally does not alter the
    separately earned Community or Club periods. It may shorten Academy access
    after a withdrawal, unlike the paid activation endpoint.
    """
    member = await _load_member_for_update(db, auth_id=auth_id)
    if not member.membership:
        if payload.paid_until is None:
            return member
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    member.membership.academy_paid_until = (
        _as_aware(payload.paid_until) if payload.paid_until else None
    )
    if payload.paid_until:
        member.membership.declared_tiers = sorted(
            set(member.membership.declared_tiers or ["community"]) | {"academy"}
        )
    _normalize_stored_tiers(member)
    logger.info(
        "Academy projection applied: member=%s paid_until=%s source=%s",
        member.id,
        payload.paid_until,
        payload.source_reference,
    )

    await db.commit()
    return await _response_member(db, member.id)
