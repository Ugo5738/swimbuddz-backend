"""Admin endpoints for logging outreach touchpoints against a contact."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateDeal,
    CorporateTouchpoint,
)
from services.corporate_service.schemas import (
    CorporateTouchpointCreate,
    CorporateTouchpointResponse,
)

router = APIRouter(tags=["admin-corporate-touchpoints"])


@router.post(
    "/contacts/{contact_id}/touchpoints",
    response_model=CorporateTouchpointResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_touchpoint(
    contact_id: uuid.UUID,
    payload: CorporateTouchpointCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Log a touchpoint (email, call, demo, note) against a contact."""
    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Corporate contact not found")

    if payload.deal_id is not None:
        deal = (
            await db.execute(
                select(CorporateDeal).where(
                    CorporateDeal.id == payload.deal_id,
                    CorporateDeal.contact_id == contact_id,
                )
            )
        ).scalar_one_or_none()
        if not deal:
            raise HTTPException(
                status_code=400,
                detail="deal_id does not belong to this contact",
            )

    touchpoint = CorporateTouchpoint(
        contact_id=contact_id,
        deal_id=payload.deal_id,
        type=payload.type,
        direction=payload.direction,
        occurred_at=payload.occurred_at or utc_now(),
        summary=payload.summary,
        outcome=payload.outcome,
        next_action=payload.next_action,
        logged_by_auth_id=current_user.user_id,
    )
    db.add(touchpoint)

    # Cascade: update the deal's last_touch_at if one was named.
    if payload.deal_id is not None:
        deal.last_touch_at = touchpoint.occurred_at  # type: ignore[union-attr]

    await db.commit()
    await db.refresh(touchpoint)
    return touchpoint


@router.get(
    "/contacts/{contact_id}/touchpoints",
    response_model=List[CorporateTouchpointResponse],
)
async def list_touchpoints(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all touchpoints for a contact, newest first."""
    contact_exists = (
        await db.execute(
            select(CorporateContact.id).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact_exists:
        raise HTTPException(status_code=404, detail="Corporate contact not found")

    result = await db.execute(
        select(CorporateTouchpoint)
        .where(CorporateTouchpoint.contact_id == contact_id)
        .order_by(CorporateTouchpoint.occurred_at.desc())
    )
    return list(result.scalars().all())
