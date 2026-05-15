"""QR-code self check-in endpoint (volunteer scans at the pool)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    SlotStatus,
    VolunteerOpportunity,
    VolunteerSlot,
)
from services.volunteer_service.schemas import (
    QrCheckinRequest,
    QrCheckinResponse,
    VolunteerSlotResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import _QR_CHECKIN_AFTER_MINUTES, _QR_CHECKIN_BEFORE_MINUTES

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/qr-checkin", response_model=QrCheckinResponse)
async def qr_checkin(
    data: QrCheckinRequest,
    user: Annotated[AuthUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    """Self check-in via QR code scanned at the pool.

    The QR code encodes a token unique to the opportunity.
    The volunteer must be authenticated and have an active (CLAIMED or APPROVED)
    slot for the opportunity. Check-in is allowed within a time window around
    the opportunity start time.
    """
    # 1. Look up opportunity by QR token
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(
                VolunteerOpportunity.qr_token == data.token,
                VolunteerOpportunity.qr_checkin_enabled.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not opp:
        raise HTTPException(status_code=404, detail="Invalid or expired QR code")

    # 2. Validate time window
    now = datetime.now(timezone.utc)
    if opp.start_time:
        opp_start = datetime.combine(opp.date, opp.start_time, tzinfo=timezone.utc)
        window_open = opp_start - timedelta(minutes=_QR_CHECKIN_BEFORE_MINUTES)
        window_close = opp_start + timedelta(minutes=_QR_CHECKIN_AFTER_MINUTES)
        if now < window_open:
            raise HTTPException(
                status_code=400,
                detail=f"Check-in opens {_QR_CHECKIN_BEFORE_MINUTES} minutes before the session starts",
            )
        if now > window_close:
            raise HTTPException(
                status_code=400,
                detail="Check-in window has closed for this session",
            )
    else:
        # No start_time — allow check-in anytime on the opportunity date
        opp_date_start = datetime.combine(opp.date, dt_time.min, tzinfo=timezone.utc)
        opp_date_end = opp_date_start + timedelta(days=1)
        if not (opp_date_start <= now < opp_date_end):
            raise HTTPException(
                status_code=400,
                detail="Check-in is only available on the day of the session",
            )

    # 3. Resolve member
    member = await get_member_by_auth_id(user.user_id, calling_service="volunteer")
    if not member:
        raise HTTPException(status_code=404, detail="Member profile not found")
    member_id = uuid.UUID(member["id"])

    # 4. Find the member's active slot for this opportunity
    slot = (
        await db.execute(
            select(VolunteerSlot).where(
                VolunteerSlot.opportunity_id == opp.id,
                VolunteerSlot.member_id == member_id,
                VolunteerSlot.status.in_([SlotStatus.CLAIMED, SlotStatus.APPROVED]),
            )
        )
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(
            status_code=400,
            detail="You don't have an active volunteer slot for this session",
        )

    # 5. Idempotent: already checked in
    if slot.checked_in_at:
        member_name = (
            f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
        )
        return QrCheckinResponse(
            slot=VolunteerSlotResponse.model_validate(slot),
            opportunity_title=opp.title,
            message=f"You're already checked in, {member_name}!",
        )

    # 6. Check in
    slot.checked_in_at = now
    await db.commit()
    await db.refresh(slot)

    member_name = (
        f"{member.get('first_name', '')} {member.get('last_name', '')}".strip()
    )
    logger.info(
        "QR check-in: %s checked in to '%s' (slot %s)",
        member_name,
        opp.title,
        slot.id,
    )
    return QrCheckinResponse(
        slot=VolunteerSlotResponse.model_validate(slot),
        opportunity_title=opp.title,
        message=f"You're checked in, {member_name}! Thank you for volunteering.",
    )
