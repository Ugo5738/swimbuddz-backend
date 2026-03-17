"""Personal notifications router: CRUD for member notifications + dispatch endpoint."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.communications_service.models import Notification, NotificationPreferences
from services.communications_service.schemas import (
    NotificationDispatchRequest,
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

# Maps notification category to the NotificationPreferences field that controls email
CATEGORY_TO_EMAIL_PREF: dict[str, str] = {
    "sessions": "email_session_reminders",
    "academy": "email_academy_updates",
    "payments": "email_payment_receipts",
    "store": "email_payment_receipts",  # reuse payment receipts pref for now
    "coaching": "email_coach_messages",
    "announcements": "email_announcements",
}


# ============================================================================
# MEMBER-FACING ENDPOINTS
# ============================================================================


@router.get("/", response_model=NotificationListResponse)
async def list_notifications(
    member_id: uuid.UUID = Query(..., description="Member ID"),
    category: Optional[str] = Query(None, description="Filter by category"),
    unread_only: bool = Query(False, description="Only return unread"),
    limit: int = Query(20, ge=1, le=50, description="Max items to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_async_db),
):
    """List notifications for a member, newest first."""
    now = datetime.now(timezone.utc)

    # Base query: non-expired notifications for this member
    base_where = [
        Notification.member_id == member_id,
    ]
    # Exclude expired
    base_where.append(
        (Notification.expires_at.is_(None)) | (Notification.expires_at > now)
    )

    if category:
        base_where.append(Notification.category == category)
    if unread_only:
        base_where.append(Notification.read_at.is_(None))

    # Items query
    items_query = (
        select(Notification)
        .where(*base_where)
        .order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(items_query)
    items = result.scalars().all()

    # Total count
    total_query = select(func.count(Notification.id)).where(*base_where)
    total_result = await db.execute(total_query)
    total = total_result.scalar_one() or 0

    # Unread count (always for this member, regardless of filters)
    unread_query = select(func.count(Notification.id)).where(
        Notification.member_id == member_id,
        (Notification.expires_at.is_(None)) | (Notification.expires_at > now),
        Notification.read_at.is_(None),
    )
    unread_result = await db.execute(unread_query)
    unread_count = unread_result.scalar_one() or 0

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        unread_count=unread_count,
    )


@router.get("/unread-count", response_model=NotificationUnreadCountResponse)
async def get_unread_count(
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Get unread notification count for a member."""
    now = datetime.now(timezone.utc)
    query = select(func.count(Notification.id)).where(
        Notification.member_id == member_id,
        (Notification.expires_at.is_(None)) | (Notification.expires_at > now),
        Notification.read_at.is_(None),
    )
    result = await db.execute(query)
    return NotificationUnreadCountResponse(unread_count=result.scalar_one() or 0)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: uuid.UUID,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark a single notification as read."""
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.member_id == member_id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if not notification.read_at:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(notification)

    return notification


@router.post("/read-all")
async def mark_all_notifications_read(
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark all unread notifications as read for a member."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(Notification)
        .where(
            Notification.member_id == member_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    result = await db.execute(stmt)
    await db.commit()
    return {"marked_read": result.rowcount or 0}


@router.delete("/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: uuid.UUID,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Dismiss/delete a single notification."""
    query = select(Notification).where(
        Notification.id == notification_id,
        Notification.member_id == member_id,
    )
    result = await db.execute(query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    await db.delete(notification)
    await db.commit()


# ============================================================================
# SERVICE-TO-SERVICE DISPATCH
# ============================================================================


@router.post("/dispatch", status_code=201)
async def dispatch_notification(
    payload: NotificationDispatchRequest,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create and deliver notification(s). Called by other services via the gateway.

    Always creates an in-app notification row. Optionally sends email if the
    member's preferences allow it.
    """
    created_count = 0

    for member_id in payload.member_ids:
        # 1. Always create in-app notification
        notification = Notification(
            member_id=member_id,
            type=payload.type,
            category=payload.category,
            title=payload.title,
            body=payload.body,
            icon=payload.icon,
            action_url=payload.action_url,
            metadata_=payload.metadata,
            expires_at=payload.expires_at,
        )
        db.add(notification)
        created_count += 1

        # 2. Send email if requested and member preferences allow
        if "email" in payload.channels and payload.email_template:
            try:
                prefs_query = select(NotificationPreferences).where(
                    NotificationPreferences.member_id == member_id
                )
                prefs_result = await db.execute(prefs_query)
                prefs = prefs_result.scalar_one_or_none()

                # Check preference — default to True if no prefs row exists
                pref_field = CATEGORY_TO_EMAIL_PREF.get(payload.category)
                should_email = True
                if prefs and pref_field:
                    should_email = getattr(prefs, pref_field, True)

                if should_email:
                    # Use the existing email router's send_email function
                    from libs.common.emails.core import send_email

                    # Build email from template data
                    email_data = payload.email_data or {}
                    to_email = email_data.get("to_email")
                    if to_email:
                        await send_email(
                            to_email=to_email,
                            subject=payload.title,
                            html_content=email_data.get(
                                "html_content", payload.body or ""
                            ),
                        )
            except Exception as e:
                logger.warning(
                    "Failed to send notification email for member %s: %s",
                    member_id,
                    e,
                )

    await db.commit()
    return {"dispatched": created_count}


# ============================================================================
# ADMIN ENDPOINTS
# ============================================================================


@router.get("/admin/stats")
async def get_notification_stats(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get notification volume statistics."""
    now = datetime.now(timezone.utc)

    total_query = select(func.count(Notification.id))
    total_result = await db.execute(total_query)

    unread_query = select(func.count(Notification.id)).where(
        Notification.read_at.is_(None),
    )
    unread_result = await db.execute(unread_query)

    return {
        "total_notifications": total_result.scalar_one() or 0,
        "total_unread": unread_result.scalar_one() or 0,
    }


@router.post("/admin/cleanup")
async def cleanup_expired_notifications(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Purge expired notifications."""
    now = datetime.now(timezone.utc)
    stmt = delete(Notification).where(
        Notification.expires_at.isnot(None),
        Notification.expires_at <= now,
    )
    result = await db.execute(stmt)
    await db.commit()
    return {"purged": result.rowcount or 0}
