import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import Announcement
from services.communications_service.schemas import (  # , AnnouncementCreate
    AnnouncementResponse,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/announcements", tags=["announcements"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/", response_model=List[AnnouncementResponse])
async def list_announcements(
    db: AsyncSession = Depends(get_async_db),
):
    """
    List all announcements, newest first.
    """
    query = select(Announcement).order_by(
        Announcement.is_pinned.desc(), Announcement.published_at.desc()
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/stats")
async def get_announcement_stats(
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get announcement statistics.
    """
    # Just total count for now
    query = select(func.count(Announcement.id))
    result = await db.execute(query)
    recent_announcements_count = result.scalar_one() or 0

    return {"recent_announcements_count": recent_announcements_count}


@router.get("/{announcement_id}", response_model=AnnouncementResponse)
async def get_announcement(
    announcement_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get details of a specific announcement.
    """
    query = select(Announcement).where(Announcement.id == announcement_id)
    result = await db.execute(query)
    announcement = result.scalar_one_or_none()

    if not announcement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Announcement not found",
        )
    return announcement


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


from fastapi import Query

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

    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count

    return ContentPostResponse.model_validate(post_dict)


@content_router.post("/", response_model=ContentPostResponse, status_code=201)
async def create_content_post(
    post_data: ContentPostCreate,
    # TODO: Get created_by from authentication
    created_by: uuid.UUID = Query(..., description="Admin member ID creating the post"),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new content post (admin only)."""
    from datetime import datetime

    post = ContentPost(
        **post_data.model_dump(exclude={"is_published"}),
        created_by=created_by,
        is_published=post_data.is_published,
        published_at=datetime.utcnow() if post_data.is_published else None,
    )

    db.add(post)
    await db.commit()
    await db.refresh(post)

    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = 0

    return ContentPostResponse.model_validate(post_dict)


@content_router.patch("/{post_id}", response_model=ContentPostResponse)
async def update_content_post(
    post_id: uuid.UUID,
    post_data: ContentPostUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a content post (admin only)."""
    from datetime import datetime

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
        post.published_at = datetime.utcnow()

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

    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count

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
    comments = result.scalars().all()

    return [ContentCommentResponse.model_validate(comment) for comment in comments]


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
    comments = result.scalars().all()

    return [AnnouncementCommentResponse.model_validate(comment) for comment in comments]
