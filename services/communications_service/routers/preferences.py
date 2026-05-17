"""Notification preferences for the communications service.

Settings rows are keyed by `member_auth_id` (Supabase auth ID stored as a
string), matching the convention used by payments_service. The previous
`member_id: UUID` design compared two unrelated UUIDs (DB-internal
Member.id vs auth_id) and could never match — every read returned 404
or raised AttributeError on the broken `current_user.id` access.

Removed in this revision:

  * `GET /preferences/{member_id}` — used `current_user.id` (non-existent)
    AND compared `member_id` to it (wrong UUID type). Never worked. The
    self-access case is covered by `/me`; the admin case will return as
    a separate `require_admin`-gated endpoint when there's a real
    admin-tooling use case.

  * `POST /preferences/check-opt-in` — was publicly exposed via the
    gateway with NO auth dependency at all, accepting an arbitrary
    `member_id` query param. Zero internal callers in the codebase.
    If other services need to check opt-in before sending, add a new
    internal endpoint gated by `require_service_role` with an explicit
    caller list.
"""

from fastapi import APIRouter, Depends
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


async def _get_or_create_prefs(
    auth_id: str, db: AsyncSession
) -> NotificationPreferences:
    """Return the prefs row for `auth_id`, creating defaults on first read."""
    result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.member_auth_id == auth_id
        )
    )
    prefs = result.scalar_one_or_none()
    if prefs is None:
        prefs = NotificationPreferences(member_auth_id=auth_id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


@router.get("/me", response_model=NotificationPreferencesResponse)
async def get_my_preferences(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the current user's notification preferences.

    Creates a row with all-defaults the first time a member accesses
    their preferences. Subsequent calls return the persisted row.
    """
    return await _get_or_create_prefs(current_user.user_id, db)


@router.patch("/me", response_model=NotificationPreferencesResponse)
async def update_my_preferences(
    updates: NotificationPreferencesUpdate,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update the current user's notification preferences.

    Only fields included in the request body are written; unset fields
    keep their current value. Auto-creates the row if it doesn't exist.
    """
    prefs = await _get_or_create_prefs(current_user.user_id, db)
    for field, value in updates.model_dump(exclude_unset=True).items():
        setattr(prefs, field, value)
    await db.commit()
    await db.refresh(prefs)
    return prefs
