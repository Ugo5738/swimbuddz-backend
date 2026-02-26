"""Shared dependencies and helper functions for transport service routers."""

import httpx
from fastapi import Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.transport_service.models import MemberRef
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_current_member(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> MemberRef:
    """Resolve the authenticated Supabase user to a transport MemberRef."""
    result = await db.execute(
        select(MemberRef).where(MemberRef.auth_id == current_user.user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )
    return member


def _raise_wallet_error(e: httpx.HTTPStatusError) -> None:
    """Convert wallet HTTP errors into user-friendly FastAPI exceptions."""
    if e.response.status_code == 400:
        detail = e.response.json().get("detail", "")
        if "Insufficient" in detail:
            raise HTTPException(
                status_code=402,
                detail="Insufficient Bubbles. Please top up your wallet.",
            )
        if "frozen" in detail.lower() or "suspended" in detail.lower():
            raise HTTPException(
                status_code=403,
                detail="Wallet is inactive. Please contact support.",
            )
    raise HTTPException(
        status_code=502, detail="Payment service error. Please try again."
    )
