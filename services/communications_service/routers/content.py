"""Communications content router: content posts and comments."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user, get_optional_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.member_utils import resolve_members_basic
from libs.common.service_client import emit_rewards_event, get_member_by_id
from libs.db.session import get_async_db
from services.communications_service.models import (
    ContentComment,
    ContentCommentLike,
    ContentPost,
    ContentPostEmailLog,
)
from services.communications_service.schemas import (
    CommentCreate,
    ContentAIDraftCreate,
    ContentCommentResponse,
    ContentCommentReactionResponse,
    ContentPostCreate,
    ContentPostResponse,
    ContentPostUpdate,
)
from services.communications_service.services.content_access import (
    allowed_content_tiers,
    require_content_read_access,
    resolve_content_actor,
)
from services.communications_service.services.content_ai import (
    ContentAIDraftError,
    generate_content_draft,
)
from services.communications_service.tasks.content_publishing import (
    send_content_post_publish_emails,
)

content_router = APIRouter(prefix="/content", tags=["content"])
logger = get_logger(__name__)

# ============================================================================
# CONTENT POST ENDPOINTS
# ============================================================================


async def _content_email_stats(
    db: AsyncSession,
    post_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict]:
    if not post_ids:
        return {}

    result = await db.execute(
        select(ContentPostEmailLog).where(ContentPostEmailLog.post_id.in_(post_ids))
    )
    stats: dict[uuid.UUID, dict] = {
        post_id: {
            "email_sent_count": 0,
            "email_failed_count": 0,
            "email_in_progress_count": 0,
            "email_unknown_count": 0,
            "email_attempt_count": 0,
            "last_email_sent_at": None,
        }
        for post_id in post_ids
    }
    for log in result.scalars().all():
        post_stats = stats.setdefault(
            log.post_id,
            {
                "email_sent_count": 0,
                "email_failed_count": 0,
                "email_in_progress_count": 0,
                "email_unknown_count": 0,
                "email_attempt_count": 0,
                "last_email_sent_at": None,
            },
        )
        post_stats["email_attempt_count"] += log.attempt_count or 0
        if log.delivery_status == "sent":
            post_stats["email_sent_count"] += 1
            if log.sent_at and (
                post_stats["last_email_sent_at"] is None
                or log.sent_at > post_stats["last_email_sent_at"]
            ):
                post_stats["last_email_sent_at"] = log.sent_at
        elif log.delivery_status == "failed":
            post_stats["email_failed_count"] += 1
        elif log.delivery_status in {"pending", "sending"}:
            post_stats["email_in_progress_count"] += 1
        elif log.delivery_status == "unknown":
            post_stats["email_unknown_count"] += 1
    return stats


def _with_email_stats(post_dict: dict, stats: dict | None) -> dict:
    post_dict.update(
        stats
        or {
            "email_sent_count": 0,
            "email_failed_count": 0,
            "email_in_progress_count": 0,
            "email_unknown_count": 0,
            "email_attempt_count": 0,
            "last_email_sent_at": None,
            "email_recipient_snapshot_at": None,
            "email_dispatch_last_attempt_at": None,
            "email_dispatch_completed_at": None,
            "email_dispatch_last_error": None,
        }
    )
    return post_dict


def _redact_content_admin_fields(post_dict: dict) -> dict:
    """Remove editorial and delivery internals from non-admin responses."""
    post_dict.update(
        {
            "featured_image_prompt": None,
            "ai_request_id": None,
            "ai_context_version": None,
            "ai_model_used": None,
            "email_on_publish": False,
            "email_sent_count": 0,
            "email_failed_count": 0,
            "email_in_progress_count": 0,
            "email_unknown_count": 0,
            "email_attempt_count": 0,
            "last_email_sent_at": None,
            "email_recipient_snapshot_at": None,
            "email_dispatch_last_attempt_at": None,
            "email_dispatch_completed_at": None,
            "email_dispatch_last_error": None,
        }
    )
    return post_dict


async def _emit_content_published_reward(post: ContentPost) -> None:
    try:
        member = await get_member_by_id(
            str(post.created_by), calling_service="communications"
        )
        if member and member.get("auth_id"):
            await emit_rewards_event(
                event_type="content.published",
                member_auth_id=member["auth_id"],
                member_id=str(post.created_by),
                service_source="communications",
                event_data={
                    "post_title": post.title,
                    "category": post.category,
                },
                idempotency_key=f"content-published-{post.id}",
                calling_service="communications",
            )
    except Exception:
        logger.warning(
            "Failed to emit content.published reward for post %s",
            post.id,
            exc_info=True,
        )


async def _content_post_response(
    db: AsyncSession,
    post: ContentPost,
    *,
    include_admin_fields: bool = False,
) -> ContentPostResponse:
    comment_query = select(func.count(ContentComment.id)).where(
        ContentComment.post_id == post.id
    )
    comment_result = await db.execute(comment_query)
    comment_count = comment_result.scalar_one()

    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = comment_count
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )
    if include_admin_fields:
        email_stats = await _content_email_stats(db, [post.id])
        _with_email_stats(post_dict, email_stats.get(post.id))
    else:
        _redact_content_admin_fields(post_dict)
    return ContentPostResponse.model_validate(post_dict)


@content_router.get("/", response_model=List[ContentPostResponse])
async def list_content_posts(
    category: Optional[str] = Query(None, description="Filter by category"),
    published_only: bool = Query(True, description="Show only published posts"),
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List content posts with optional filters."""
    actor = await resolve_content_actor(current_user)
    if not published_only and not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required to list drafts",
        )

    query = select(ContentPost)

    if published_only or not actor.is_admin:
        query = query.where(ContentPost.is_published.is_(True))

    if not actor.is_admin:
        tiers = allowed_content_tiers(actor)
        if not tiers:
            return []
        query = query.where(ContentPost.tier_access.in_(tiers))

    if category:
        query = query.where(ContentPost.category == category)

    query = query.order_by(ContentPost.published_at.desc())

    result = await db.execute(query)
    posts = result.scalars().all()
    post_ids = [p.id for p in posts]

    # Resolve featured image URLs
    media_ids = [p.featured_image_media_id for p in posts if p.featured_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}
    email_stats = await _content_email_stats(db, post_ids) if actor.is_admin else {}

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
        if actor.is_admin:
            _with_email_stats(post_dict, email_stats.get(post.id))
        else:
            _redact_content_admin_fields(post_dict)
        posts_with_counts.append(ContentPostResponse.model_validate(post_dict))

    return posts_with_counts


@content_router.post("/ai-drafts", response_model=ContentPostResponse, status_code=201)
async def create_ai_content_draft(
    draft_data: ContentAIDraftCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Generate and save an unpublished content post draft for admin review."""
    actor = await resolve_content_actor(current_user, require_member=True)
    assert actor.member_id is not None
    try:
        generated = await generate_content_draft(
            title=draft_data.title,
            category=draft_data.category,
            tier_access=draft_data.tier_access,
            brief=draft_data.brief,
        )
    except ContentAIDraftError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    post = ContentPost(
        title=draft_data.title,
        summary=generated.summary,
        body=generated.body,
        category=draft_data.category,
        tier_access=draft_data.tier_access,
        featured_image_prompt=generated.featured_image_prompt,
        ai_request_id=uuid.UUID(generated.ai_request_id),
        ai_context_version=generated.context_version,
        ai_model_used=generated.model_used,
        created_by=actor.member_id,
        is_published=False,
        published_at=None,
        scheduled_for=None,
        email_on_publish=False,
    )

    db.add(post)
    await db.commit()
    await db.refresh(post)

    return await _content_post_response(db, post, include_admin_fields=True)


@content_router.get("/{post_id}", response_model=ContentPostResponse)
async def get_content_post(
    post_id: uuid.UUID,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single content post by ID."""
    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")
    actor = await resolve_content_actor(current_user)
    require_content_read_access(post, actor)

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
    if actor.is_admin:
        email_stats = await _content_email_stats(db, [post.id])
        _with_email_stats(post_dict, email_stats.get(post.id))
    else:
        _redact_content_admin_fields(post_dict)

    return ContentPostResponse.model_validate(post_dict)


@content_router.post("/", response_model=ContentPostResponse, status_code=201)
async def create_content_post(
    post_data: ContentPostCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new content post (admin only)."""
    actor = await resolve_content_actor(current_user, require_member=True)
    assert actor.member_id is not None
    post_payload = post_data.model_dump(exclude={"is_published"})
    if post_data.is_published:
        post_payload["scheduled_for"] = None
    post = ContentPost(
        **post_payload,
        created_by=actor.member_id,
        is_published=post_data.is_published,
        published_at=utc_now() if post_data.is_published else None,
    )

    db.add(post)
    await db.commit()
    await db.refresh(post)

    # Best-effort: emit reward event if created as published
    if post_data.is_published:
        await _emit_content_published_reward(post)
        await send_content_post_publish_emails(db, post)
        await db.refresh(post)

    # Resolve featured image URL
    post_dict = post.__dict__.copy()
    post_dict["comment_count"] = 0
    post_dict["featured_image_url"] = await resolve_media_url(
        post.featured_image_media_id
    )
    email_stats = await _content_email_stats(db, [post.id])
    _with_email_stats(post_dict, email_stats.get(post.id))

    return ContentPostResponse.model_validate(post_dict)


@content_router.patch("/{post_id}", response_model=ContentPostResponse)
async def update_content_post(
    post_id: uuid.UUID,
    post_data: ContentPostUpdate,
    current_user: AuthUser = Depends(require_admin),
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

    # If publishing, clear any pending schedule and set published_at if needed.
    was_unpublished = not post.is_published
    if "is_published" in update_data and update_data["is_published"]:
        if not post.published_at:
            post.published_at = utc_now()
        update_data["scheduled_for"] = None

    for field, value in update_data.items():
        setattr(post, field, value)

    await db.commit()
    await db.refresh(post)

    # Best-effort: emit reward event if just published via PATCH
    if was_unpublished and post.is_published:
        await _emit_content_published_reward(post)
        await send_content_post_publish_emails(db, post)
        await db.refresh(post)

    return await _content_post_response(db, post, include_admin_fields=True)


@content_router.post("/{post_id}/publish", response_model=ContentPostResponse)
async def publish_content_post(
    post_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Post is already published",
        )

    post.is_published = True
    post.published_at = utc_now()
    post.scheduled_for = None

    await db.commit()
    await db.refresh(post)

    # Best-effort: emit content published reward event for the author
    await _emit_content_published_reward(post)
    await send_content_post_publish_emails(db, post)
    await db.refresh(post)

    return await _content_post_response(db, post, include_admin_fields=True)


@content_router.post("/{post_id}/unpublish", response_model=ContentPostResponse)
async def unpublish_content_post(
    post_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
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

    return await _content_post_response(db, post, include_admin_fields=True)


@content_router.post(
    "/{post_id}/email/retry-failed",
    response_model=ContentPostResponse,
)
async def retry_failed_content_post_emails(
    post_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Retry only deliveries the provider explicitly reported as failed."""
    post = await db.get(ContentPost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")
    if not post.is_published or not post.email_on_publish:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Publish the post with email enabled before retrying delivery",
        )

    await send_content_post_publish_emails(db, post, force_failed_retry=True)
    await db.refresh(post)
    return await _content_post_response(db, post, include_admin_fields=True)


@content_router.delete("/{post_id}", status_code=204)
async def delete_content_post(
    post_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a content post (admin only)."""
    query = select(ContentPost).where(ContentPost.id == post_id)
    result = await db.execute(query)
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    # Delete associated records first; comments/logs are service-local soft links.
    await db.execute(delete(ContentComment).where(ContentComment.post_id == post_id))
    await db.execute(
        delete(ContentPostEmailLog).where(ContentPostEmailLog.post_id == post_id)
    )
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
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add a comment to a content post."""
    # Verify post exists
    post_query = select(ContentPost).where(ContentPost.id == post_id)
    post_result = await db.execute(post_query)
    post = post_result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")
    actor = await resolve_content_actor(current_user, require_member=True)
    require_content_read_access(post, actor)
    assert actor.member_id is not None

    comment = ContentComment(
        post_id=post_id, member_id=actor.member_id, content=comment_data.content
    )

    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return ContentCommentResponse.model_validate(comment)


@content_router.get("/{post_id}/comments", response_model=List[ContentCommentResponse])
async def list_content_comments(
    post_id: uuid.UUID,
    current_user: Optional[AuthUser] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List all comments for a content post."""
    post_result = await db.execute(select(ContentPost).where(ContentPost.id == post_id))
    post = post_result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")
    actor = await resolve_content_actor(current_user)
    require_content_read_access(post, actor)

    query = (
        select(ContentComment)
        .where(ContentComment.post_id == post_id)
        .order_by(ContentComment.created_at.asc())
    )

    result = await db.execute(query)
    comments_list = result.scalars().all()

    comment_ids = [comment.id for comment in comments_list]
    like_counts: dict[uuid.UUID, int] = {}
    liked_comment_ids: set[uuid.UUID] = set()
    if comment_ids:
        like_count_rows = (
            await db.execute(
                select(
                    ContentCommentLike.comment_id,
                    func.count(ContentCommentLike.id),
                )
                .where(ContentCommentLike.comment_id.in_(comment_ids))
                .group_by(ContentCommentLike.comment_id)
            )
        ).all()
        like_counts = {
            comment_id: int(like_count) for comment_id, like_count in like_count_rows
        }
        if actor.member_id is not None:
            liked_comment_ids = set(
                (
                    await db.execute(
                        select(ContentCommentLike.comment_id).where(
                            ContentCommentLike.comment_id.in_(comment_ids),
                            ContentCommentLike.member_id == actor.member_id,
                        )
                    )
                )
                .scalars()
                .all()
            )

    # Resolve member names via HTTP to members service
    member_ids = [c.member_id for c in comments_list]
    member_map = await resolve_members_basic(member_ids) if member_ids else {}

    comments = []
    for comment in comments_list:
        resp = ContentCommentResponse.model_validate(comment)
        info = member_map.get(str(comment.member_id))
        resp.member_name = info.full_name if info else None
        resp.like_count = like_counts.get(comment.id, 0)
        resp.liked_by_me = comment.id in liked_comment_ids
        comments.append(resp)

    return comments


async def _content_comment_for_member(
    *,
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    current_user: AuthUser,
    db: AsyncSession,
) -> tuple[ContentComment, uuid.UUID]:
    post = (
        await db.execute(select(ContentPost).where(ContentPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Content post not found")

    actor = await resolve_content_actor(current_user, require_member=True)
    require_content_read_access(post, actor)
    assert actor.member_id is not None

    comment = (
        await db.execute(
            select(ContentComment).where(
                ContentComment.id == comment_id,
                ContentComment.post_id == post_id,
            )
        )
    ).scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return comment, actor.member_id


async def _content_comment_reaction_response(
    *,
    comment_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession,
) -> ContentCommentReactionResponse:
    like_count = (
        await db.execute(
            select(func.count(ContentCommentLike.id)).where(
                ContentCommentLike.comment_id == comment_id
            )
        )
    ).scalar_one()
    liked_by_me = (
        await db.execute(
            select(ContentCommentLike.id).where(
                ContentCommentLike.comment_id == comment_id,
                ContentCommentLike.member_id == member_id,
            )
        )
    ).scalar_one_or_none()
    return ContentCommentReactionResponse(
        comment_id=comment_id,
        like_count=int(like_count or 0),
        liked_by_me=liked_by_me is not None,
    )


@content_router.put(
    "/{post_id}/comments/{comment_id}/like",
    response_model=ContentCommentReactionResponse,
)
async def like_content_comment(
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Like an article comment. Repeated requests are idempotent."""
    comment, member_id = await _content_comment_for_member(
        post_id=post_id,
        comment_id=comment_id,
        current_user=current_user,
        db=db,
    )
    await db.execute(
        insert(ContentCommentLike)
        .values(
            id=uuid.uuid4(),
            comment_id=comment.id,
            member_id=member_id,
            created_at=utc_now(),
        )
        .on_conflict_do_nothing(
            index_elements=[
                ContentCommentLike.comment_id,
                ContentCommentLike.member_id,
            ]
        )
    )
    await db.commit()
    return await _content_comment_reaction_response(
        comment_id=comment.id,
        member_id=member_id,
        db=db,
    )


@content_router.delete(
    "/{post_id}/comments/{comment_id}/like",
    response_model=ContentCommentReactionResponse,
)
async def unlike_content_comment(
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove the current member's like from an article comment."""
    comment, member_id = await _content_comment_for_member(
        post_id=post_id,
        comment_id=comment_id,
        current_user=current_user,
        db=db,
    )
    await db.execute(
        delete(ContentCommentLike).where(
            ContentCommentLike.comment_id == comment.id,
            ContentCommentLike.member_id == member_id,
        )
    )
    await db.commit()
    return await _content_comment_reaction_response(
        comment_id=comment.id,
        member_id=member_id,
        db=db,
    )


@content_router.delete("/{post_id}/comments/{comment_id}", status_code=204)
async def delete_content_comment(
    post_id: uuid.UUID,
    comment_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete one article comment (admin only)."""
    result = await db.execute(
        delete(ContentComment).where(
            ContentComment.id == comment_id,
            ContentComment.post_id == post_id,
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Comment not found")
    await db.commit()
