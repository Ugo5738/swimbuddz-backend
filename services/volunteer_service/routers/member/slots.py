"""Volunteer-slot claim + cancel endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    OpportunityStatus,
    SlotStatus,
    VolunteerOpportunity,
    VolunteerProfile,
    VolunteerSlot,
    VolunteerTier,
)
from services.volunteer_service.schemas import MemberVolunteerSlotResponse
from services.volunteer_service.services import is_late_cancellation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.post(
    "/opportunities/{opp_id}/claim",
    response_model=MemberVolunteerSlotResponse,
    status_code=201,
)
async def claim_slot(
    opp_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Claim a volunteer slot on an opportunity."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])

    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opp.status not in (OpportunityStatus.OPEN, OpportunityStatus.IN_PROGRESS):
        raise HTTPException(
            status_code=400, detail="Opportunity is not accepting claims"
        )
    if opp.slots_filled >= opp.slots_needed:
        raise HTTPException(status_code=400, detail="All slots are filled")

    # Check if already claimed
    existing = (
        await db.execute(
            select(VolunteerSlot).where(
                VolunteerSlot.opportunity_id == opp_id,
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status.in_(
                    [
                        SlotStatus.CLAIMED,
                        SlotStatus.APPROVED,
                    ]
                ),
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="You already have an active slot for this opportunity",
        )

    # Check volunteer profile + tier
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
        )
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=400, detail="Register as a volunteer first")

    tier_order = {
        VolunteerTier.TIER_1: 1,
        VolunteerTier.TIER_2: 2,
        VolunteerTier.TIER_3: 3,
    }
    if tier_order.get(profile.tier, 1) < tier_order.get(opp.min_tier, 1):
        raise HTTPException(
            status_code=403,
            detail=f"This opportunity requires {opp.min_tier.value} or higher",
        )

    # Create slot
    initial_status = (
        SlotStatus.APPROVED
        if opp.opportunity_type.value == "open_claim"
        else SlotStatus.CLAIMED
    )
    slot = VolunteerSlot(
        opportunity_id=opp_id,
        member_id=member_id,
        status=initial_status,
        approved_at=(utc_now() if initial_status == SlotStatus.APPROVED else None),
    )
    db.add(slot)

    # Update slots_filled
    opp.slots_filled += 1
    if opp.slots_filled >= opp.slots_needed:
        opp.status = OpportunityStatus.FILLED

    await db.commit()
    await db.refresh(slot)
    return slot


@router.delete("/opportunities/{opp_id}/claim", status_code=204)
async def cancel_my_claim(
    opp_id: uuid.UUID,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel my claim on an opportunity."""
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])

    slot = (
        await db.execute(
            select(VolunteerSlot).where(
                VolunteerSlot.opportunity_id == opp_id,
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status.in_([SlotStatus.CLAIMED, SlotStatus.APPROVED]),
            )
        )
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="No active claim found")

    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()

    # Track late cancellation
    if opp and is_late_cancellation(
        opp.date, opp.start_time, opp.cancellation_deadline_hours
    ):
        profile = (
            await db.execute(
                select(VolunteerProfile).where(VolunteerProfile.member_id == member_id)
            )
        ).scalar_one_or_none()
        if profile:
            profile.total_late_cancellations += 1

    slot.status = SlotStatus.CANCELLED
    slot.cancelled_at = utc_now()

    # Decrement slots_filled
    if opp and opp.slots_filled > 0:
        opp.slots_filled -= 1
        if opp.status == OpportunityStatus.FILLED:
            opp.status = OpportunityStatus.OPEN

    await db.commit()
