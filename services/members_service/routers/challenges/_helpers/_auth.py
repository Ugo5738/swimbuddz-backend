"""Auth/identity helpers for challenge routes.

Admin UUID coercion, member-id resolution from JWT, pod-lead kind
lookup, review authorization, prerequisite-badge enforcement.
"""

import uuid
from typing import List, Literal, Optional

from fastapi import HTTPException
from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from services.members_service.models import (
    ChallengeBadgeAward,
    ClubChallenge,
    Member,
    Pod,
    PodAssignment,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)


def _admin_uuid_or_none(admin: AuthUser) -> Optional[uuid.UUID]:
    """Coerce admin.user_id (string from JWT sub) to UUID for storage.

    Human admins have a Supabase user UUID; service-role tokens carry
    "service:<name>" which is not a UUID — return None in that case.
    """
    try:
        return uuid.UUID(admin.user_id)
    except (ValueError, TypeError):
        return None


async def _resolve_member_id_from_auth(
    auth_user: AuthUser, db: AsyncSession
) -> uuid.UUID:
    """Look up the local Member.id for an authenticated user.

    Raises 403 if the JWT identity has no local member profile (e.g. a
    coach-only login or a stale session). Member writes always go through
    this lookup so we never trust a member_id passed by the client.
    """
    if not auth_user.user_id:
        raise HTTPException(status_code=403, detail="Member profile not found")

    member_row = await db.execute(
        select(Member).where(Member.auth_id == auth_user.user_id)
    )
    member = member_row.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=403, detail="Member profile not found")
    return member.id


async def _resolve_member_id_from_auth_optional(
    auth_user: AuthUser, db: AsyncSession
) -> Optional[uuid.UUID]:
    """Same as _resolve_member_id_from_auth but returns None instead of 403
    when the auth identity has no local member profile.

    Used for the delegated-review path where SwimBuddz admins (whitelisted
    by email, no member row) AND pod leads (always have a member row)
    both pass through. Admins fall through this returning None and are
    accepted by the upstream `is_admin_or_service` check; pod leads
    continue with their resolved member id.
    """
    if not auth_user.user_id:
        return None
    member_row = await db.execute(
        select(Member).where(Member.auth_id == auth_user.user_id)
    )
    member = member_row.scalar_one_or_none()
    return member.id if member else None


async def _pod_lead_kind_for_member(
    *,
    reviewer_member_id: uuid.UUID,
    submitter_member_id: uuid.UUID,
    db: AsyncSession,
) -> Optional[Literal["pod_lead", "assistant_pod_lead"]]:
    """Return the reviewer's role within the submitter's active pod, if any.

    Looks up:
      1. The submitter's currently-active pod (pod_assignments with
         left_at IS NULL — the unique constraint enforces 'one pod per
         member at a time' so this is exactly 0 or 1 row).
      2. Whether the reviewer is the lead or assistant lead of that pod.

    Returns None if:
      * the submitter has no active pod (e.g. Community member, or
        between pods)
      * the reviewer isn't the lead/assistant lead of the submitter's pod
      * the reviewer would be reviewing their OWN submission (they can't)

    Pod leads can never approve their own submission — that guard is
    here, not at the upstream auth check, because the lead may or may
    not be a member of their own pod's roster (admins/coaches typically
    aren't on the roster of the pod they lead).
    """
    if reviewer_member_id == submitter_member_id:
        return None

    # Find the submitter's active pod
    assignment_row = await db.execute(
        select(PodAssignment.pod_id).where(
            PodAssignment.member_id == submitter_member_id,
            PodAssignment.left_at.is_(None),
        )
    )
    pod_id = assignment_row.scalar_one_or_none()
    if pod_id is None:
        return None

    pod_row = await db.execute(
        select(Pod.pod_lead_id, Pod.assistant_pod_lead_id).where(Pod.id == pod_id)
    )
    pod = pod_row.first()
    if pod is None:
        return None

    if pod.pod_lead_id == reviewer_member_id:
        return "pod_lead"
    if pod.assistant_pod_lead_id == reviewer_member_id:
        return "assistant_pod_lead"
    return None


async def _authorize_review(
    *,
    reviewer: AuthUser,
    challenge: ClubChallenge,
    submitter_member_id: uuid.UUID,
    db: AsyncSession,
) -> Literal["admin", "pod_lead", "assistant_pod_lead"]:
    """Authorize a review action and return the reviewer's role kind.

    Authority rules:
      * SwimBuddz admins / service-role tokens — can review ANY submission.
      * For competition-format challenges (high stakes, public winner
        reveal) — admin only. Pod leads can't approve competitions.
      * For everything else (participatory + ladder challenges) — the
        Pod Lead or Assistant Pod Lead of the submitter's active pod
        can approve.

    Returns "admin" | "pod_lead" | "assistant_pod_lead" so the caller
    can stamp `reviewed_by_kind` on the audit trail.
    Raises 403 if neither path applies.
    """
    if is_admin_or_service(reviewer):
        return "admin"

    if challenge.format == "competition":
        # Competitions stay HQ-only. Pod leads can't designate winners or
        # approve competition entries — too easy to game otherwise.
        raise HTTPException(
            status_code=403,
            detail="Only SwimBuddz admins can review competition submissions.",
        )

    reviewer_member_id = await _resolve_member_id_from_auth_optional(reviewer, db)
    if reviewer_member_id is None:
        raise HTTPException(
            status_code=403,
            detail="Reviewer must be an admin or a Pod Lead.",
        )

    kind = await _pod_lead_kind_for_member(
        reviewer_member_id=reviewer_member_id,
        submitter_member_id=submitter_member_id,
        db=db,
    )
    if kind is None:
        raise HTTPException(
            status_code=403,
            detail=("You can only review submissions from members in a pod you lead."),
        )
    return kind


async def _enforce_prerequisite(
    db: AsyncSession,
    *,
    prerequisite_id: uuid.UUID,
    member_ids: List[uuid.UUID],
) -> None:
    """Reject the submission if any member is missing the prerequisite badge.

    Used by the soft-progression-with-opt-in-hard-gating model: when a
    challenge has `requires_challenge_id` set, every member on the
    submission roster must have an approved badge for the prerequisite
    challenge before they can attempt this one. Reads from the
    denormalised `challenge_badge_awards` table for a fast indexed check.

    Raises 400 with a list of member ids that are missing the badge so
    the frontend can surface a helpful error.
    """
    if not member_ids:
        return

    # Pull award rows for any member on the roster that already has the
    # prerequisite. Members not in this set are missing the badge.
    rows = await db.execute(
        select(ChallengeBadgeAward.member_id).where(
            ChallengeBadgeAward.challenge_id == prerequisite_id,
            ChallengeBadgeAward.member_id.in_(member_ids),
        )
    )
    earned = {row[0] for row in rows.all()}
    missing = [mid for mid in member_ids if mid not in earned]
    if not missing:
        return

    # Resolve the prerequisite's title for a friendly message.
    prereq_title_row = await db.execute(
        select(ClubChallenge.title).where(ClubChallenge.id == prerequisite_id)
    )
    prereq_title = prereq_title_row.scalar_one_or_none() or "the prerequisite"

    raise HTTPException(
        status_code=400,
        detail=(
            f"This challenge requires '{prereq_title}' to be completed first. "
            f"{len(missing)} team member"
            f"{'' if len(missing) == 1 else 's'} ha"
            f"{'sn' if len(missing) == 1 else 'ven'}'t earned it yet."
        ),
    )
