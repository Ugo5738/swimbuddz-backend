"""Server-side identity and tier decisions for content posts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.session_access import active_paid_tiers
from libs.common.service_client import get_member_by_auth_id, get_member_membership

from services.communications_service.models import ContentPost


@dataclass(frozen=True)
class ContentActor:
    member_id: uuid.UUID | None
    paid_tiers: frozenset[str]
    is_admin: bool
    is_authenticated: bool


async def resolve_content_actor(
    user: AuthUser | None,
    *,
    require_member: bool = False,
) -> ContentActor:
    """Resolve an authenticated identity to server-owned content access state."""
    if user is None:
        return ContentActor(
            member_id=None,
            paid_tiers=frozenset(),
            is_admin=False,
            is_authenticated=False,
        )
    is_admin = is_admin_or_service(user)
    if is_admin and not require_member:
        return ContentActor(
            member_id=None,
            paid_tiers=frozenset(),
            is_admin=True,
            is_authenticated=True,
        )

    member = await get_member_by_auth_id(user.user_id, calling_service="communications")
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )

    member_id = uuid.UUID(str(member["id"]))
    membership = await get_member_membership(
        str(member_id), calling_service="communications"
    )
    return ContentActor(
        member_id=member_id,
        paid_tiers=frozenset(active_paid_tiers(membership or {})),
        is_admin=is_admin,
        is_authenticated=True,
    )


def allowed_content_tiers(actor: ContentActor) -> set[str]:
    if actor.is_admin:
        return {"community", "club", "academy"}
    return {"community"} | (set(actor.paid_tiers) & {"community", "club", "academy"})


def can_read_content(post: ContentPost, actor: ContentActor) -> bool:
    if actor.is_admin:
        return True
    if not post.is_published:
        return False
    tier = str(post.tier_access or "").strip().lower()
    return tier in allowed_content_tiers(actor)


def require_content_read_access(post: ContentPost, actor: ContentActor) -> None:
    """Fail as not-found so inaccessible drafts and tiers are not enumerable."""
    if not can_read_content(post, actor):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Content post not found",
        )
