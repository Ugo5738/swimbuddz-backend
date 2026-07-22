"""Membership authorization shared by member-facing reporting routes."""

from fastapi import Depends, HTTPException, status

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id, get_member_membership


async def require_active_community_membership(
    current_user: AuthUser = Depends(get_current_user),
) -> dict:
    """Require effective Community access (direct or inherited)."""
    member = await get_member_by_auth_id(
        current_user.user_id,
        calling_service="reporting",
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An active Community membership is required to view reports.",
        )
    membership = await get_member_membership(
        str(member["id"]),
        calling_service="reporting",
    )
    effective_tiers = (membership or {}).get("effective_paid_tiers")
    # Compatibility fallback supports a rolling deployment where reporting may
    # briefly talk to an older members-service instance.
    if effective_tiers is None:
        effective_tiers = (membership or {}).get("active_tiers") or []
    if "community" not in effective_tiers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An active Community membership is required to view reports.",
        )
    return member
