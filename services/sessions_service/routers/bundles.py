"""Session bundle cart endpoints.

A "bundle cart" is a temporary selection of multiple sessions a member wants
to book together. The cart is created when the member hits Checkout from the
Sessions Hub multi-select, and is used to load the selected sessions on the
bundle checkout page.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.sessions_service.models import SessionBundleCart

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

    cart = SessionBundleCart(
        id=uuid.uuid4(),
        member_auth_id=current_user.user_id,
        session_ids=unique_ids,
        status="open",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=CART_TTL_HOURS),
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
