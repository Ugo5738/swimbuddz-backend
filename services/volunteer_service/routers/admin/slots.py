"""Admin: slot listing, update, check-in/out, no-show, bulk-complete."""

import uuid
from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    SlotStatus,
    VolunteerHoursLog,
    VolunteerOpportunity,
    VolunteerProfile,
    VolunteerSlot,
)
from services.volunteer_service.schemas import (
    BulkCompleteRequest,
    CheckoutSlotRequest,
    VolunteerSlotAdminUpdate,
    VolunteerSlotResponse,
)
from services.volunteer_service.services import (
    compute_reliability_score,
    update_profile_aggregates,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ._helpers import _auto_checkout_if_past, _emit_volunteer_reward, _enrich_slot

router = APIRouter()


@router.get("/opportunities/{opp_id}/slots", response_model=list[VolunteerSlotResponse])
async def list_slots(
    opp_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    # Load opportunity for auto-checkout evaluation
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == opp_id)
        )
    ).scalar_one_or_none()

    rows = (
        (
            await db.execute(
                select(VolunteerSlot)
                .where(VolunteerSlot.opportunity_id == opp_id)
                .order_by(VolunteerSlot.claimed_at.asc())
            )
        )
        .scalars()
        .all()
    )

    # Lazy auto-checkout: complete slots past the opportunity end time
    if opp:
        for slot in rows:
            await _auto_checkout_if_past(db, slot, opp)

    return [await _enrich_slot(s) for s in rows]


@router.patch("/slots/{slot_id}", response_model=VolunteerSlotResponse)
async def update_slot(
    slot_id: uuid.UUID,
    data: VolunteerSlotAdminUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    if data.status == SlotStatus.APPROVED:
        slot.status = SlotStatus.APPROVED
        slot.approved_at = utc_now()
        _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
        admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
        slot.approved_by = admin_member_id
    elif data.status == SlotStatus.REJECTED:
        slot.status = SlotStatus.REJECTED
        # Decrement filled count
        opp = (
            await db.execute(
                select(VolunteerOpportunity).where(
                    VolunteerOpportunity.id == slot.opportunity_id
                )
            )
        ).scalar_one_or_none()
        if opp and opp.slots_filled > 0:
            opp.slots_filled -= 1
    elif data.status:
        slot.status = data.status

    if data.admin_notes is not None:
        slot.admin_notes = data.admin_notes

    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot)


@router.post("/slots/{slot_id}/checkin", response_model=VolunteerSlotResponse)
async def checkin_slot(
    slot_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status not in (SlotStatus.CLAIMED, SlotStatus.APPROVED):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Slot must be claimed or approved to check in",
        )

    opp = (
        await db.execute(
            select(VolunteerOpportunity).where(
                VolunteerOpportunity.id == slot.opportunity_id
            )
        )
    ).scalar_one_or_none()
    if opp and opp.end_time:
        end_dt = datetime.combine(opp.date, opp.end_time, tzinfo=timezone.utc)
        if utc_now() > end_dt:
            raise HTTPException(
                status_code=400,
                detail=(
                    "This opportunity has already ended. Use 'No-Show' "
                    "or 'Complete All' with the actual hours instead."
                ),
            )

    slot.checked_in_at = utc_now()
    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot)


@router.post("/slots/{slot_id}/checkout", response_model=VolunteerSlotResponse)
async def checkout_slot(
    slot_id: uuid.UUID,
    data: CheckoutSlotRequest = None,
    admin: Annotated[AuthUser, Depends(require_admin)] = None,
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if not slot.checked_in_at:
        raise HTTPException(status_code=400, detail="Must check in before checking out")

    now = utc_now()
    slot.checked_out_at = now
    slot.status = SlotStatus.COMPLETED

    # Calculate hours
    if data and data.hours:
        slot.hours_logged = data.hours
    else:
        delta = now - slot.checked_in_at
        slot.hours_logged = round(delta.total_seconds() / 3600, 2)

    if data and data.admin_notes:
        slot.admin_notes = data.admin_notes

    # Create hours log entry
    opp = (
        await db.execute(
            select(VolunteerOpportunity)
            .options(selectinload(VolunteerOpportunity.role))
            .where(VolunteerOpportunity.id == slot.opportunity_id)
        )
    ).scalar_one_or_none()

    _admin = (
        await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
        if admin
        else None
    )
    hours_log = VolunteerHoursLog(
        member_id=slot.member_id,
        slot_id=slot.id,
        opportunity_id=slot.opportunity_id,
        hours=slot.hours_logged,
        date=opp.date if opp else date.today(),
        role_id=opp.role_id if opp else None,
        source="slot_completion",
        logged_by=uuid.UUID(_admin["id"]) if _admin else None,
    )
    db.add(hours_log)

    await db.commit()

    # Update profile aggregates
    await update_profile_aggregates(db, slot.member_id)
    await db.commit()

    # Best-effort: emit rewards event
    await _emit_volunteer_reward(slot, opp)

    await db.refresh(slot)
    return await _enrich_slot(slot)


@router.post("/slots/{slot_id}/no-show", response_model=VolunteerSlotResponse)
async def mark_no_show(
    slot_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    slot.status = SlotStatus.NO_SHOW

    # Update profile
    profile = (
        await db.execute(
            select(VolunteerProfile).where(VolunteerProfile.member_id == slot.member_id)
        )
    ).scalar_one_or_none()
    if profile:
        profile.total_no_shows += 1
        profile.reliability_score = compute_reliability_score(
            profile.total_no_shows, profile.total_late_cancellations
        )

    await db.commit()
    await db.refresh(slot)
    return await _enrich_slot(slot)


@router.post("/slots/bulk-complete", response_model=list[VolunteerSlotResponse])
async def bulk_complete(
    data: BulkCompleteRequest,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Complete a batch of slots, deriving timestamps and hours from the
    opportunity's scheduled window.

    For each slot:
    - reject if the opportunity hasn't started yet (admin mis-click);
    - preserve any pre-existing `checked_in_at` (a real check-in stamp
      shouldn't be clobbered); otherwise stamp the opportunity start;
    - set `checked_out_at` to the opportunity's end (or `now()` if the
      event is still in progress / has no end_time);
    - derive `hours_logged` from those timestamps so the slot row stays
      internally consistent. `data.hours`, if supplied, overrides the
      derivation for every slot in the batch.
    """
    results: list[tuple[VolunteerSlot, VolunteerOpportunity | None]] = []
    _admin = await get_member_by_auth_id(admin.user_id, calling_service="volunteer")
    admin_member_id = uuid.UUID(_admin["id"]) if _admin else None
    now = utc_now()

    for slot_id in data.slot_ids:
        slot = (
            await db.execute(select(VolunteerSlot).where(VolunteerSlot.id == slot_id))
        ).scalar_one_or_none()
        if not slot:
            continue
        # Idempotency: skip slots that are already in a terminal state, so
        # re-clicking Complete All doesn't add duplicate hours_log rows.
        if slot.status not in (SlotStatus.CLAIMED, SlotStatus.APPROVED):
            continue

        opp = (
            await db.execute(
                select(VolunteerOpportunity)
                .options(selectinload(VolunteerOpportunity.role))
                .where(VolunteerOpportunity.id == slot.opportunity_id)
            )
        ).scalar_one_or_none()

        opp_start_dt: datetime | None = None
        opp_end_dt: datetime | None = None
        if opp and opp.start_time:
            opp_start_dt = datetime.combine(opp.date, opp.start_time, tzinfo=timezone.utc)
        if opp and opp.end_time:
            opp_end_dt = datetime.combine(opp.date, opp.end_time, tzinfo=timezone.utc)

        # Future-event guard: don't let an admin "complete" something that
        # hasn't started. (We do allow completing in-progress events — admin
        # may be closing volunteers out as they leave.)
        if opp_start_dt and now < opp_start_dt:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot complete '{opp.title if opp else 'opportunity'}': "
                    "it hasn't started yet."
                ),
            )

        # checked_in_at: preserve a real check-in; otherwise stamp opp start
        if slot.checked_in_at is None:
            slot.checked_in_at = opp_start_dt or now
        # checked_out_at: opp end if past, else clamp to now
        if opp_end_dt:
            slot.checked_out_at = min(opp_end_dt, now)
        else:
            slot.checked_out_at = now
        slot.status = SlotStatus.COMPLETED

        # hours: explicit override > derived from the stamps we just set.
        # If both stamps collapsed to the same `now` (no opp times at all),
        # fall back to 2.0 so we don't credit zero hours.
        if data.hours is not None:
            slot.hours_logged = data.hours
        else:
            delta_hours = (
                slot.checked_out_at - slot.checked_in_at
            ).total_seconds() / 3600
            slot.hours_logged = round(delta_hours, 2) if delta_hours > 0 else 2.0

        hours_log = VolunteerHoursLog(
            member_id=slot.member_id,
            slot_id=slot.id,
            opportunity_id=slot.opportunity_id,
            hours=slot.hours_logged,
            date=opp.date if opp else date.today(),
            role_id=opp.role_id if opp else None,
            source="slot_completion",
            logged_by=admin_member_id,
        )
        db.add(hours_log)
        results.append((slot, opp))

    await db.commit()

    # Update aggregates for each member
    member_ids = {s.member_id for s, _ in results}
    for mid in member_ids:
        await update_profile_aggregates(db, mid)
    await db.commit()

    # Best-effort: emit rewards events for each completed slot
    for slot, opp in results:
        await _emit_volunteer_reward(slot, opp)

    enriched = []
    for slot, _ in results:
        await db.refresh(slot)
        enriched.append(await _enrich_slot(slot))
    return enriched
