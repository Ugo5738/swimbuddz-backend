import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from libs.auth.dependencies import get_optional_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.member_utils import resolve_members_basic
from libs.db.session import get_async_db
from services.communications_service.models import (
    Announcement,
    AnnouncementAudience,
    AnnouncementCategory,
    AnnouncementStatus,
    NotificationPreferences,
)
from services.communications_service.schemas import (  # , AnnouncementCreate
    AnnouncementCreate,
    AnnouncementResponse,
    AnnouncementUpdate,
)
from services.communications_service.templates.messaging import send_message_email
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
logger = get_logger(__name__)
router = APIRouter(prefix="/announcements", tags=["announcements"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def _is_admin(user: Optional[AuthUser]) -> bool:
    if not user:
        return False
    is_service_role = user.role == "service_role"
    has_admin_role = user.has_role("admin")
    is_whitelisted_email = user.email is not None and user.email in (
        settings.ADMIN_EMAILS or []
    )
    return is_service_role or has_admin_role or is_whitelisted_email


def _default_expiry(category: AnnouncementCategory) -> Optional[datetime]:
    now = datetime.now(timezone.utc)
    if category in (
        AnnouncementCategory.RAIN_UPDATE,
        AnnouncementCategory.SCHEDULE_CHANGE,
    ):
        return now + timedelta(hours=24)
    return None


def _default_notification_flags(category: AnnouncementCategory) -> tuple[bool, bool]:
    if category in (
        AnnouncementCategory.RAIN_UPDATE,
        AnnouncementCategory.SCHEDULE_CHANGE,
    ):
        return True, True  # email + push
    if category in (AnnouncementCategory.EVENT, AnnouncementCategory.COMPETITION):
        return True, False  # email by default
    return True, False


async def _get_allowed_audiences(authorization: Optional[str]) -> Set[str]:
    # Guests only see community announcements.
    if not authorization:
        return {"community"}

    url = f"{settings.MEMBERS_SERVICE_URL}/members/me"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url, headers={"Authorization": authorization})
        if response.status_code != 200:
            return {"community"}
        data = response.json()
    except httpx.RequestError:
        return {"community"}

    membership = data.get("membership") or {}
    active_tiers = membership.get("active_tiers") or []
    primary_tier = membership.get("primary_tier")
    tiers = {str(t).lower() for t in active_tiers if t}
    if primary_tier:
        tiers.add(str(primary_tier).lower())

    # Higher tiers inherit lower tier announcements.
    if "academy" in tiers:
        tiers.add("club")
    tiers.add("community")
    return tiers


def _pref_allows_email(
    category: AnnouncementCategory, pref: Optional[NotificationPreferences]
) -> bool:
    if not pref:
        return True
    if category == AnnouncementCategory.ACADEMY_UPDATE:
        return pref.email_academy_updates
    return pref.email_announcements


async def _fetch_members_for_audience(audience: AnnouncementAudience) -> list[dict]:
    url = f"{settings.MEMBERS_SERVICE_URL}/members/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params={"skip": 0, "limit": 1000})
        if response.status_code != 200:
            logger.warning("Failed to fetch members list for announcement delivery.")
            return []
        members = response.json()
    except httpx.RequestError as exc:
        logger.warning("Failed to reach members service: %s", exc)
        return []

    if audience == AnnouncementAudience.COMMUNITY:
        return members

    filtered = []
    async with httpx.AsyncClient(timeout=10) as client:
        for member in members:
            auth_id = member.get("auth_id")
            if not auth_id:
                continue
            try:
                detail_resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{auth_id}"
                )
                if detail_resp.status_code != 200:
                    continue
                detail = detail_resp.json()
            except httpx.RequestError:
                continue

            membership = detail.get("membership") or {}
            active_tiers = membership.get("active_tiers") or []
            primary_tier = membership.get("primary_tier")
            tiers = {str(t).lower() for t in active_tiers if t}
            if primary_tier:
                tiers.add(str(primary_tier).lower())
            if "academy" in tiers:
                tiers.add("club")

            if audience.value in tiers:
                filtered.append(member)

    return filtered


async def _send_announcement_emails(
    announcement: Announcement,
    db: AsyncSession,
) -> None:
    if (
        announcement.status != AnnouncementStatus.PUBLISHED
        or not announcement.notify_email
    ):
        return

    members = await _fetch_members_for_audience(announcement.audience)
    if not members:
        return

    member_ids = [m.get("id") for m in members if m.get("id")]
    pref_map: dict[str, NotificationPreferences] = {}
    if member_ids:
        prefs_result = await db.execute(
            select(NotificationPreferences).where(
                NotificationPreferences.member_id.in_(member_ids)
            )
        )
        pref_map = {str(p.member_id): p for p in prefs_result.scalars().all()}

    subject = f"SwimBuddz Update: {announcement.title}"
    body = announcement.body
    if announcement.summary:
        body = f"{announcement.summary}\n\n{announcement.body}"

    sent_count = 0
    for member in members:
        email = member.get("email")
        member_id = str(member.get("id")) if member.get("id") else None
        if not email:
            continue
        pref = pref_map.get(member_id) if member_id else None
        if not _pref_allows_email(announcement.category, pref):
            continue
        success = await send_message_email(
            to_email=email,
            subject=subject,
            body=body,
        )
        if success:
            sent_count += 1

    if sent_count:
        logger.info("Sent announcement email to %s recipients", sent_count)


@router.get("/", response_model=List[AnnouncementResponse])
async def list_announcements(
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all announcements, newest first.
    """
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(Announcement)
    if not include_all:
        now = datetime.now(timezone.utc)
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )

    query = query.order_by(
        Announcement.is_pinned.desc(), Announcement.published_at.desc()
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_announcement_stats(
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get announcement statistics.
    """
    # Just total count for now
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(func.count(Announcement.id))
    if not include_all:
        now = datetime.now(timezone.utc)
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )
    result = await db.execute(query)
    recent_announcements_count = result.scalar_one() or 0

    return {"recent_announcements_count": recent_announcements_count}


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: uuid.UUID,
    request: Request,
    include_all: bool = Query(
        False, description="Include drafts/archived/expired (admin only)"
    ),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific announcement.
    """
    if include_all and not _is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )

    query = select(Announcement).where(Announcement.id == announcement_id)
    if not include_all:
        now = datetime.now(timezone.utc)
        allowed_audiences = await _get_allowed_audiences(
            request.headers.get("authorization")
        )
        query = query.where(
            Announcement.status == AnnouncementStatus.PUBLISHED,
            or_(Announcement.expires_at.is_(None), Announcement.expires_at > now),
            Announcement.audience.in_(allowed_audiences),
        )
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )
    return announcement


@router.post("/", response_model=AnnouncementResponse, status_code=201)
async def create_announcement(
    announcement_data: AnnouncementCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a new announcement (Admin only).
    """
    payload = announcement_data.model_dump()
    status_value = payload.get("status", AnnouncementStatus.PUBLISHED)

    published_at = payload.get("published_at")
    if status_value == AnnouncementStatus.PUBLISHED and not published_at:
        published_at = datetime.now(timezone.utc)
    elif status_value == AnnouncementStatus.DRAFT:
        published_at = None

    expires_at = payload.get("expires_at")
    if not expires_at and status_value == AnnouncementStatus.PUBLISHED:
        expires_at = _default_expiry(
            payload.get("category", AnnouncementCategory.GENERAL)
        )

    notify_email = payload.get("notify_email")
    notify_push = payload.get("notify_push")
    if notify_email is None or notify_push is None:
        default_email, default_push = _default_notification_flags(
            payload.get("category", AnnouncementCategory.GENERAL)
        )
        if notify_email is None:
            notify_email = default_email
        if notify_push is None:
            notify_push = default_push

    announcement = Announcement(
        **payload,
        published_at=published_at,
        expires_at=expires_at,
        notify_email=notify_email,
        notify_push=notify_push,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)
    await _send_announcement_emails(announcement, db)
    return announcement


@router.patch("/{announcement_id}", response_model=AnnouncementResponse)
async def update_announcement(
    announcement_id: uuid.UUID,
    announcement_data: AnnouncementUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update an announcement (Admin only).
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )

    update_data = announcement_data.model_dump(exclude_unset=True)
    if "status" in update_data:
        if (
            update_data["status"] == AnnouncementStatus.PUBLISHED
            and not announcement.published_at
        ):
            announcement.published_at = datetime.now(timezone.utc)
        elif update_data["status"] == AnnouncementStatus.DRAFT:
            announcement.published_at = None
        if (
            update_data["status"] == AnnouncementStatus.PUBLISHED
            and "expires_at" not in update_data
            and not announcement.expires_at
        ):
            category_value = update_data.get("category", announcement.category)
            announcement.expires_at = _default_expiry(category_value)

    for field, value in update_data.items():
        setattr(announcement, field, value)

    await db.commit()
    await db.refresh(announcement)
    if (
        "status" in update_data
        and update_data["status"] == AnnouncementStatus.PUBLISHED
    ):
        await _send_announcement_emails(announcement, db)
    return announcement


@router.delete("/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete an announcement (Admin only).
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )

    # Delete comments and reads first
    await db.execute(
        delete(AnnouncementComment).where(
            AnnouncementComment.announcement_id == announcement_id
        )
    )
    await db.execute(
        delete(AnnouncementRead).where(
            AnnouncementRead.announcement_id == announcement_id
        )
    )
    await db.delete(announcement)
    await db.commit()


# ===== ANNOUNCEMENT READ TRACKING =====
from services.communications_service.models import (
    AnnouncementCategoryConfig,
    AnnouncementRead,
)
from services.communications_service.schemas import (
    AnnouncementCategoryConfigCreate,
    AnnouncementCategoryConfigResponse,
    AnnouncementCategoryConfigUpdate,
    AnnouncementReadCreate,
    AnnouncementReadResponse,
)


@router.post(
    "/{announcement_id}/read", response_model=AnnouncementReadResponse, status_code=201
)
async def mark_announcement_read(
    announcement_id: uuid.UUID,
    read_data: AnnouncementReadCreate,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an announcement as read by a member.
    If already read, updates acknowledged status if provided.
    """
    # Verify announcement exists
    announcement_query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(announcement_query)
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Announcement not found")

    # Check if already read
    existing_query = select(AnnouncementRead).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.member_id == member_id,
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        # Update acknowledged status if provided
        if read_data.acknowledged and not existing.acknowledged:
            existing.acknowledged = True
            existing.acknowledged_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(existing)
        return existing

    # Create new read record
    read_record = AnnouncementRead(
        announcement_id=announcement_id,
        member_id=member_id,
        acknowledged=read_data.acknowledged,
        acknowledged_at=datetime.now(timezone.utc) if read_data.acknowledged else None,
    )
    db.add(read_record)
    await db.commit()
    await db.refresh(read_record)
    return read_record


@router.get("/{announcement_id}/read-status", response_model=AnnouncementReadResponse)
async def get_announcement_read_status(
    announcement_id: uuid.UUID,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get read status of an announcement for a specific member.
    """
    query = select(AnnouncementRead).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.member_id == member_id,
    )
    result = await db.execute(query)
    read_record = result.scalar_one_or_none()

    if not read_record:
        raise HTTPException(status_code=404, detail="Read status not found")

    return read_record


@router.get("/{announcement_id}/read-stats")
async def get_announcement_read_stats(
    announcement_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get read/acknowledged statistics for an announcement (Admin only).
    """
    read_count_query = select(func.count(AnnouncementRead.id)).where(
        AnnouncementRead.announcement_id == announcement_id
    )
    acknowledged_count_query = select(func.count(AnnouncementRead.id)).where(
        AnnouncementRead.announcement_id == announcement_id,
        AnnouncementRead.acknowledged.is_(True),
    )

    read_result = await db.execute(read_count_query)
    ack_result = await db.execute(acknowledged_count_query)

    return {
        "announcement_id": announcement_id,
        "read_count": read_result.scalar_one() or 0,
        "acknowledged_count": ack_result.scalar_one() or 0,
    }


# ===== CUSTOM CATEGORY CONFIGURATION =====
category_router = APIRouter(prefix="/categories", tags=["announcement-categories"])


@category_router.get("/", response_model=List[AnnouncementCategoryConfigResponse])
async def list_announcement_categories(
    include_inactive: bool = Query(False, description="Include inactive categories"),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all custom announcement categories.
    """
    query = select(AnnouncementCategoryConfig)
    if not include_inactive:
        query = query.where(AnnouncementCategoryConfig.is_active.is_(True))
    query = query.order_by(AnnouncementCategoryConfig.display_name)

    result = await db.execute(query)
    return result.scalars().all()


@category_router.post(
    "/", response_model=AnnouncementCategoryConfigResponse, status_code=201
)
async def create_announcement_category(
    category_data: AnnouncementCategoryConfigCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a custom announcement category (Admin only).
    """
    # Check for duplicate name
    existing_query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.name == category_data.name.lower().replace(" ", "_")
    )
    existing_result = await db.execute(existing_query)
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail="Category with this name already exists"
        )

    category = AnnouncementCategoryConfig(
        name=category_data.name.lower().replace(" ", "_"),
        display_name=category_data.display_name,
        description=category_data.description,
        auto_expire_hours=category_data.auto_expire_hours,
        default_notify_email=category_data.default_notify_email,
        default_notify_push=category_data.default_notify_push,
        icon=category_data.icon,
        color=category_data.color,
    )
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


@category_router.patch(
    "/{category_id}", response_model=AnnouncementCategoryConfigResponse
)
async def update_announcement_category(
    category_id: uuid.UUID,
    category_data: AnnouncementCategoryConfigUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Update a custom announcement category (Admin only).
    """
    query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.id == category_id
    )
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    update_data = category_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)

    await db.commit()
    await db.refresh(category)
    return category


@category_router.delete("/{category_id}", status_code=204)
async def delete_announcement_category(
    category_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete a custom announcement category (Admin only).
    Note: This will not delete announcements using this category.
    """
    query = select(AnnouncementCategoryConfig).where(
        AnnouncementCategoryConfig.id == category_id
    )
    result = await db.execute(query)
    category = result.scalar_one_or_none()

    if not category:
        raise HTTPException(status_code=404, detail="Category not found")

    await db.delete(category)
    await db.commit()


from typing import List, Optional

# ===== CONTENT POST ENDPOINTS =====
from services.communications_service.models import (
    AnnouncementComment,
    ContentComment,
    ContentPost,
)
from services.communications_service.schemas import (
    AnnouncementCommentResponse,
    CommentCreate,
    ContentCommentResponse,
    ContentPostCreate,
    ContentPostResponse,
    ContentPostUpdate,
)


@admin_router.delete("/members/{member_id}")
async def admin_delete_member_comments(
    member_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete member comments in communications service (Admin only).
    """
    content_result = await db.execute(
        delete(ContentComment).where(ContentComment.member_id == member_id)
    )
    announcement_result = await db.execute(
        delete(AnnouncementComment).where(AnnouncementComment.member_id == member_id)
    )
    await db.commit()
    return {
        "deleted_content_comments": content_result.rowcount or 0,
        "deleted_announcement_comments": announcement_result.rowcount or 0,
    }


content_router = APIRouter(prefix="/content", tags=["content"])


@content_router.get("/", response_model=List[ContentPostResponse])
async def list_content_posts(
    category: Optional[str] = Query(None, description="Filter by category"),
    published_only: bool = Query(True, description="Show only published posts"),
    db: AsyncSession = Depends(get_async_db),
):
    """List content posts with optional filters."""
    query = select(ContentPost)

    if published_only:
        query = query.where(ContentPost.is_published.is_(True))

    if category:
        query = query.where(ContentPost.category == category)

    query = query.order_by(ContentPost.published_at.desc())

    result = await db.execute(query)
    posts = result.scalars().all()

    # Resolve featured image URLs
    media_ids = [p.featured_image_media_id for p in posts if p.featured_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    # Get comment counts for each post
    posts_with_counts = []
    for post in posts:
        comment_query = select(func.count(ContentComment.id)).where(
            ContentComment.post_id == post.id
        )
        comment_result = await db.execute(comment_query)
        comment_count = comment_result.scalar_one()

        post_dict = post.__dict__.copy()
        post_dict["comment_count"] = comment_count
        # Add resolved URL
        if post.featured_image_media_id:
            post_dict["featured_image_url"] = url_map.get(post.featured_image_media_id)
        posts_with_counts.append(ContentPostResponse.model_validate(post_dict))

    return posts_with_counts


@content_router.get("/{post_id}", response_model=ContentPostResponse)
async def get_content_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single content post by ID."""
    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    # Get comment count
    comment_query = select(func.count(ContentComment.id)).where(
        ContentComment.post_id == post.id
    )
    comment_result = await db.execute(comment_query)
    comment_count = comment_result.scalar_one()

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )

    return ContentPostResponse.model_validate(post_dict)


@content_router.post("/", response_model=ContentPostResponse, status_code=201)
async def create_content_post(
    post_data: ContentPostCreate,
    # TODO: Get created_by from authentication
    created_by: uuid.UUID = Query(..., description="Admin member ID creating the post"),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new content post (admin only)."""
    from datetime import datetime, timezone

    post = ContentPost(
        **post_data.model_dump(exclude={"is_published"}),
        created_by=created_by,
        is_published=post_data.is_published,
        published_at=datetime.now(timezone.utc) if post_data.is_published else None,
    )

    db.add(post)
    await db.commit()
    await db.refresh(post)

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = 0
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )

    return ContentPostResponse.model_validate(post_dict)


@content_router.patch("/{post_id}", response_model=ContentPostResponse)
async def update_content_post(
    post_id: uuid.UUID,
    post_data: ContentPostUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a content post (admin only)."""
    from datetime import datetime, timezone

    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    # Update only provided fields
    update_data = post_data.model_dump(exclude_unset=True)

    # If publishing for the first time, set published_at
    if (
        "is_published" in update_data
        and update_data["is_published"]
        and not post.published_at
    ):
        post.published_at = datetime.now(timezone.utc)

    for field, value in update_data.items():
        setattr(post, field, value)

    await db.commit()
    await db.refresh(post)

    # Get comment count
    comment_query = select(func.count(ContentComment.id)).where(
        ContentComment.post_id == post.id
    )
    comment_result = await db.execute(comment_query)
    comment_count = comment_result.scalar_one()

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )

    return ContentPostResponse.model_validate(post_dict)


@content_router.post("/{post_id}/publish", response_model=ContentPostResponse)
async def publish_content_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Publish a content post (admin only).
    Sets is_published to True and published_at to current time.
    """
    from datetime import datetime, timezone

    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    if post.is_published:
        raise HTTPException(status_code=400, detail="Post is already published")

    post.is_published = True
    post.published_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(post)

    # Get comment count
    comment_query = select(func.count(ContentComment.id)).where(
        ContentComment.post_id == post.id
    )
    comment_result = await db.execute(comment_query)
    comment_count = comment_result.scalar_one()

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )

    return ContentPostResponse.model_validate(post_dict)


@content_router.post("/{post_id}/unpublish", response_model=ContentPostResponse)
async def unpublish_content_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Unpublish a content post (admin only).
    Sets is_published to False while preserving published_at for history.
    """
    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    if not post.is_published:
        raise HTTPException(status_code=400, detail="Post is not published")

    post.is_published = False

    await db.commit()
    await db.refresh(post)

    # Get comment count
    comment_query = select(func.count(ContentComment.id)).where(
        ContentComment.post_id == post.id
    )
    comment_result = await db.execute(comment_query)
    comment_count = comment_result.scalar_one()

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )

    return ContentPostResponse.model_validate(post_dict)


@content_router.delete("/{post_id}", status_code=204)
async def delete_content_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a content post (admin only)."""
    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    # Delete associated comments first
    await db.execute(select(ContentComment).where(ContentComment.post_id == post_id))
    await db.delete(post)
    await db.commit()

    return None


# ===== CONTENT COMMENT ENDPOINTS =====
@content_router.post(
    "/{post_id}/comments", response_model=ContentCommentResponse, status_code=201
)
async def create_content_comment(
    post_id: uuid.UUID,
    comment_data: CommentCreate,
    # TODO: Get member_id from authentication
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a comment to a content post."""
    # Verify post exists
    post_query = select(ContentPost).where(ContentPost.id == post_id)
    post_result = await db.execute(post_query)
    post = post_result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    comment = ContentComment(
        post_id=post_id, member_id=member_id, content=comment_data.content
    )

    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return ContentCommentResponse.model_validate(comment)


@content_router.get("/{post_id}/comments", response_model=List[ContentCommentResponse])
async def list_content_comments(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """List all comments for a content post."""
    query = (
        select(ContentComment)
        .where(ContentComment.post_id == post_id)
        .order_by(ContentComment.created_at.asc())
    )

    result = await db.execute(query)
    comments_list = result.scalars().all()

    # Resolve member names via HTTP to members service
    member_ids = [c.member_id for c in comments_list]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    comments = []
    for comment in comments_list:
        resp = ContentCommentResponse.model_validate(comment)
        info = member_map.get(str(comment.member_id))
        resp.member_name = info.full_name if info else None
        comments.append(resp)

    return comments


# ===== ANNOUNCEMENT COMMENT ENDPOINTS =====
@router.post(
    "/{announcement_id}/comments",
    response_model=AnnouncementCommentResponse,
    status_code=201,
)
async def create_announcement_comment(
    announcement_id: uuid.UUID,
    comment_data: CommentCreate,
    # TODO: Get member_id from authentication
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a comment to an announcement."""
    # Verify announcement exists
    announcement_query = select(Announcement).where(Announcement.id == announcement_id)
    announcement_result = await db.execute(announcement_query)
    announcement = announcement_result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(status_code=404, detail="Announcement not found")

    comment = AnnouncementComment(
        announcement_id=announcement_id,
        member_id=member_id,
        content=comment_data.content,
    )

    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return AnnouncementCommentResponse.model_validate(comment)


@router.get(
    "/{announcement_id}/comments", response_model=List[AnnouncementCommentResponse]
)
async def list_announcement_comments(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """List all comments for an announcement."""
    query = (
        select(AnnouncementComment)
        .where(AnnouncementComment.announcement_id == announcement_id)
        .order_by(AnnouncementComment.created_at.asc())
    )

    result = await db.execute(query)
    comments_list = result.scalars().all()

    # Resolve member names via HTTP to members service
    member_ids = [c.member_id for c in comments_list]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    comments = []
    for comment in comments_list:
        resp = AnnouncementCommentResponse.model_validate(comment)
        info = member_map.get(str(comment.member_id))
        resp.member_name = info.full_name if info else None
        comments.append(resp)

    return comments
