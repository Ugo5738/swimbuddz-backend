"""
Notification preferences router for the Communications Service.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import NotificationPreferences
from services.communications_service.schemas import (
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("/me", response_model=NotificationPreferencesResponse)
async def get_my_preferences(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the current user's notification preferences.
    Creates default preferences if none exist.
    """
    query = select(NotificationPreferences).where(
        NotificationPreferences.member_id == current_user.id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Create default preferences for this user
        prefs = NotificationPreferences(member_id=current_user.id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)

    return prefs


@router.patch("/me", response_model=NotificationPreferencesResponse)
async def update_my_preferences(
    updates: NotificationPreferencesUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update the current user's notification preferences.
    """
    query = select(NotificationPreferences).where(
        NotificationPreferences.member_id == current_user.id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Create with provided values
        prefs = NotificationPreferences(
            member_id=current_user.id,
            **updates.model_dump(exclude_unset=True),
        )
        db.add(prefs)
    else:
        # Update existing
        for field, value in updates.model_dump(exclude_unset=True).items():
            setattr(prefs, field, value)

    await db.commit()
    await db.refresh(prefs)

    return prefs


@router.get("/{member_id}", response_model=NotificationPreferencesResponse)
async def get_member_preferences(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get notification preferences for a specific member.
    Admin only or self-access.
    """
    # Check authorization - admin or self
    if current_user.id != member_id and current_user.role not in ["admin", "service"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own notification preferences",
        )

    query = select(NotificationPreferences).where(
        NotificationPreferences.member_id == member_id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    if not prefs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification preferences not found for this member",
        )

    return prefs


@router.post("/check-opt-in")
async def check_notification_opt_in(
    member_id: uuid.UUID,
    notification_type: str,  # e.g., "email_announcements", "push_session_reminders"
    db: AsyncSession = Depends(get_async_db),
):
    """
    Check if a member has opted in for a specific notification type.
    Used by other services before sending notifications.
    """
    query = select(NotificationPreferences).where(
        NotificationPreferences.member_id == member_id
    )
    result = await db.execute(query)
    prefs = result.scalar_one_or_none()

    # If no preferences exist, use defaults (all true except marketing)
    if not prefs:
        # Default preferences
        defaults = {
            "email_announcements": True,
            "email_session_reminders": True,
            "email_academy_updates": True,
            "email_payment_receipts": True,
            "email_coach_messages": True,
            "email_marketing": False,
            "push_announcements": True,
            "push_session_reminders": True,
            "push_academy_updates": True,
            "push_coach_messages": True,
            "weekly_digest": True,
        }
        opted_in = defaults.get(notification_type, True)
    else:
        opted_in = getattr(prefs, notification_type, True)

    return {
        "member_id": str(member_id),
        "notification_type": notification_type,
        "opted_in": opted_in,
    }
