"""Session bundle cart endpoints.

A "bundle cart" is a temporary selection of multiple sessions a member wants
to book together. The cart is created when the member hits Checkout from the
Sessions Hub multi-select, and is used to load the selected sessions on the
bundle checkout page.
"""

import uuid
from datetime import datetime, timedelta
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_auth_id
from libs.common.session_access import denial_message
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionBundleCart
from services.sessions_service.services.session_access import (
    evaluate_member_session_access,
)

router = APIRouter(prefix="/sessions/bundles", tags=["bundles"])

# Cart TTL — if not paid/checked out within 24 hours, cart is stale.
CART_TTL_HOURS = 24
# Max sessions per bundle cart.
MAX_BUNDLE_SIZE = 10


class CreateBundleCartRequest(BaseModel):
    session_ids: List[uuid.UUID] = Field(..., min_length=1, max_length=MAX_BUNDLE_SIZE)


class BundleCartResponse(BaseModel):
    id: uuid.UUID
    member_auth_id: str
    session_ids: List[str]
    status: str
    created_at: datetime
    expires_at: datetime | None


@router.post(
    "",
    response_model=BundleCartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bundle_cart(
    payload: CreateBundleCartRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> BundleCartResponse:
    """Create a new bundle cart with the selected session IDs."""
    # De-duplicate while preserving order
    seen: set[str] = set()
    unique_ids: List[str] = []
    for sid in payload.session_ids:
        s = str(sid)
        if s not in seen:
            seen.add(s)
            unique_ids.append(s)

    if len(unique_ids) < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one session_id is required",
        )
    if len(unique_ids) > MAX_BUNDLE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_BUNDLE_SIZE} sessions per bundle",
        )

    try:
        member = await get_member_by_auth_id(
            current_user.user_id, calling_service="sessions"
        )
    except httpx.HTTPError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify your member profile. Please try again.",
        )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Complete registration first.",
        )
    member_id = uuid.UUID(member["id"])

    session_ids = [uuid.UUID(sid) for sid in unique_ids]
    result = await db.execute(select(Session).where(Session.id.in_(session_ids)))
    sessions_by_id = {str(session.id): session for session in result.scalars().all()}
    missing = [sid for sid in unique_ids if sid not in sessions_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more selected sessions could not be found.",
        )

    now = utc_now()
    for sid in unique_ids:
        session = sessions_by_id[sid]
        access = await evaluate_member_session_access(
            session=session,
            member_id=member_id,
            now=now,
        )
        if not access.bookable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{session.title}: {denial_message(access.reason)}",
            )

    cart = SessionBundleCart(
        id=uuid.uuid4(),
        member_auth_id=current_user.user_id,
        session_ids=unique_ids,
        status="open",
        expires_at=utc_now() + timedelta(hours=CART_TTL_HOURS),
    )
    db.add(cart)
    await db.commit()
    await db.refresh(cart)

    return BundleCartResponse(
        id=cart.id,
        member_auth_id=cart.member_auth_id,
        session_ids=cart.session_ids,
        status=cart.status,
        created_at=cart.created_at,
        expires_at=cart.expires_at,
    )


@router.get("/{bundle_id}", response_model=BundleCartResponse)
async def get_bundle_cart(
    bundle_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> BundleCartResponse:
    """Fetch a bundle cart by id. Must belong to the authenticated member."""
    stmt = select(SessionBundleCart).where(SessionBundleCart.id == bundle_id)
    result = await db.execute(stmt)
    cart = result.scalar_one_or_none()

    if not cart:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bundle cart not found"
        )
    if cart.member_auth_id != current_user.user_id:
        # Don't leak existence — 404 instead of 403.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Bundle cart not found"
        )

    return BundleCartResponse(
        id=cart.id,
        member_auth_id=cart.member_auth_id,
        session_ids=cart.session_ids,
        status=cart.status,
        created_at=cart.created_at,
        expires_at=cart.expires_at,
    )
