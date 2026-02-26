"""Communications content router: content posts and comments."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.member_utils import resolve_members_basic
from libs.db.session import get_async_db
from services.communications_service.models import ContentComment, ContentPost
from services.communications_service.schemas import (
    CommentCreate,
    ContentCommentResponse,
    ContentPostCreate,
    ContentPostResponse,
    ContentPostUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

content_router = APIRouter(prefix="/content", tags=["content"])


# ============================================================================
# CONTENT POST ENDPOINTS
# ============================================================================


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


# ============================================================================
# CONTENT COMMENT ENDPOINTS
# ============================================================================


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
