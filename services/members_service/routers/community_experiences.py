"""Quarter-specific Community Experience pricing and entitlements."""

import uuid
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user, require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    ClubEnrollment,
    CommunityExperienceOffering,
    CommunityExperiencePurchase,
    Member,
    MemberMembership,
)
from services.members_service.schemas import (
    ActivateCommunityExperienceRequest,
    CommunityExperienceOfferingCreate,
    CommunityExperienceOfferingResponse,
    CommunityExperienceQuote,
)
from services.members_service.services.membership_pricing import (
    annual_membership_extension,
)

router = APIRouter(
    prefix="/clubs/community-experiences", tags=["community-experiences"]
)
settings = get_settings()


async def _member_by_auth(db: AsyncSession, auth_id: str) -> Member:
    member = (
        await db.execute(select(Member).where(Member.auth_id == auth_id))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Member profile not found")
    return member


def _assert_purchase_window(offering: CommunityExperienceOffering) -> None:
    now = utc_now()
    if not offering.is_active:
        raise HTTPException(
            status_code=409, detail="This Community Experience is closed"
        )
    if offering.purchase_opens_at and offering.purchase_opens_at > now:
        raise HTTPException(status_code=409, detail="Purchasing has not opened yet")
    if offering.purchase_closes_at and offering.purchase_closes_at < now:
        raise HTTPException(status_code=409, detail="Purchasing has closed")


async def _quote_for_member(
    db: AsyncSession,
    *,
    offering: CommunityExperienceOffering,
    member: Member,
) -> CommunityExperienceQuote:
    _assert_purchase_window(offering)
    existing = (
        await db.execute(
            select(CommunityExperiencePurchase.id).where(
                CommunityExperiencePurchase.member_id == member.id,
                CommunityExperiencePurchase.offering_id == offering.id,
            )
        )
    ).first()
    period_start = datetime.combine(
        offering.period_start, time.min, tzinfo=timezone.utc
    )
    period_end = datetime.combine(offering.period_end, time.max, tzinfo=timezone.utc)
    active_club = (
        await db.execute(
            select(ClubEnrollment.id).where(
                ClubEnrollment.member_id == member.id,
                ClubEnrollment.status == "active",
                ClubEnrollment.starts_at <= period_end,
                ClubEnrollment.ends_at > period_start,
            )
        )
    ).first()
    membership = (
        await db.execute(
            select(MemberMembership).where(MemberMembership.member_id == member.id)
        )
    ).scalar_one_or_none()
    annual_membership_months, annual_membership_fee = annual_membership_extension(
        paid_until=(membership.community_paid_until if membership else None),
        coverage_end=offering.period_end,
        annual_fee_kobo=int(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20_000) * 100
        ),
        now=utc_now(),
    )
    if active_club:
        context = "club_member_later"
        amount = offering.club_member_fee_kobo
    else:
        context = "standard_member"
        amount = offering.standard_member_fee_kobo
    return CommunityExperienceQuote(
        offering_id=offering.id,
        offering_name=offering.name,
        member_auth_id=member.auth_id,
        currency=offering.currency,
        price_context=context,
        amount_kobo=amount,
        annual_membership_fee_kobo=annual_membership_fee,
        annual_membership_months=annual_membership_months,
        subtotal_kobo=amount + annual_membership_fee,
        already_purchased=bool(existing),
    )


@router.get("", response_model=list[CommunityExperienceOfferingResponse])
async def list_community_experiences(
    _member: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return list(
        (
            await db.execute(
                select(CommunityExperienceOffering)
                .where(CommunityExperienceOffering.is_active.is_(True))
                .order_by(CommunityExperienceOffering.period_start)
            )
        ).scalars()
    )


@router.post(
    "",
    response_model=CommunityExperienceOfferingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_community_experience(
    body: CommunityExperienceOfferingCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    offering = CommunityExperienceOffering(**body.model_dump())
    db.add(offering)
    await db.commit()
    await db.refresh(offering)
    return offering


@router.get("/{offering_id}/quote", response_model=CommunityExperienceQuote)
async def quote_community_experience(
    offering_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Community Experience not found")
    member = await _member_by_auth(db, current_user.user_id)
    return await _quote_for_member(db, offering=offering, member=member)


@router.get(
    "/internal/{offering_id}/payment-context",
    response_model=CommunityExperienceQuote,
)
async def internal_community_experience_payment_context(
    offering_id: uuid.UUID,
    member_auth_id: str,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Community Experience not found")
    member = await _member_by_auth(db, member_auth_id)
    quote = await _quote_for_member(db, offering=offering, member=member)
    if quote.already_purchased:
        raise HTTPException(
            status_code=409, detail="This Community Experience is already purchased"
        )
    return quote


@router.post(
    "/internal/{offering_id}/activate",
    response_model=CommunityExperienceOfferingResponse,
)
async def activate_community_experience(
    offering_id: uuid.UUID,
    body: ActivateCommunityExperienceRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if offering is None:
        raise HTTPException(status_code=404, detail="Community Experience not found")
    member = await _member_by_auth(db, body.member_auth_id)
    # Payment providers retry callbacks and may deliver the same activation
    # concurrently. The unique member/offering boundary keeps this idempotent.
    await db.execute(
        insert(CommunityExperiencePurchase)
        .values(
            member_id=member.id,
            offering_id=offering.id,
            price_context=body.price_context,
            amount_paid_kobo=body.amount_paid_kobo,
            payment_reference=body.payment_reference,
        )
        .on_conflict_do_nothing(
            index_elements=[
                CommunityExperiencePurchase.member_id,
                CommunityExperiencePurchase.offering_id,
            ]
        )
    )
    await db.commit()
    return offering
