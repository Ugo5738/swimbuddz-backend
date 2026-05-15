"""Shared helpers + constants for the challenges router package.

Used by every sub-router. Covers:
  * auth/identity resolution (admin uuid, member id from JWT, pod-lead kind)
  * review authorization
  * prerequisite-badge enforcement
  * media + member hydration (example media, proof media, roster names)
  * response builders (challenge / submission / public-challenge)
  * post-approval side effects (badge ledger, in-app notifs, external rewards)
"""

import uuid
from typing import List, Literal, Optional

from fastapi import HTTPException
from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.service_client import (
    dispatch_notification,
    grant_challenge_reward_bubbles,
    grant_challenge_volunteer_hours,
)
from services.members_service.models import (
    ChallengeBadgeAward,
    ChallengeExampleMedia,
    ChallengeSubmissionMedia,
    ChallengeSubmissionMember,
    ClubChallenge,
    Member,
    MemberChallengeCompletion,
    Pod,
    PodAssignment,
)
from services.members_service.schemas import (
    ChallengeExampleMediaResponse,
    ChallengePublicResponse,
    ChallengeSubmissionMediaResponse,
    ChallengeSubmissionMemberResponse,
    ChallengeSubmissionResponse,
    ChallengeWinnerPublicInfo,
    ClubChallengeResponse,
)
from sqlalchemy import func, select
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


async def _load_challenge_example_media(
    challenge_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeExampleMediaResponse]:
    """Load example media join rows for a challenge with hydrated URLs.

    Hydrates file_url via a single bulk HTTP call to media_service
    (services/libs/common/media_utils.py:resolve_media_urls). thumbnail_url
    falls back to file_url for now — the media service returns image URLs
    that work for both purposes; videos can use the same URL on a poster.
    """
    rows = await db.execute(
        select(ChallengeExampleMedia)
        .where(ChallengeExampleMedia.challenge_id == challenge_id)
        .order_by(ChallengeExampleMedia.order_idx, ChallengeExampleMedia.created_at)
    )
    items = list(rows.scalars().all())
    url_map = await resolve_media_urls([m.media_id for m in items])
    out: List[ChallengeExampleMediaResponse] = []
    for row in items:
        item = ChallengeExampleMediaResponse.model_validate(row)
        url = url_map.get(row.media_id) or url_map.get(str(row.media_id))
        if url:
            item.file_url = url
            # No separate thumbnail in the media-service response shape;
            # callers can render <video poster=...> with the same URL or
            # let the browser auto-generate.
            item.thumbnail_url = url
        out.append(item)
    return out


async def _load_submission_proof_media(
    submission_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeSubmissionMediaResponse]:
    rows = await db.execute(
        select(ChallengeSubmissionMedia)
        .where(ChallengeSubmissionMedia.submission_id == submission_id)
        .order_by(
            ChallengeSubmissionMedia.order_idx, ChallengeSubmissionMedia.created_at
        )
    )
    items = list(rows.scalars().all())
    url_map = await resolve_media_urls([m.media_id for m in items])
    out: List[ChallengeSubmissionMediaResponse] = []
    for row in items:
        item = ChallengeSubmissionMediaResponse.model_validate(row)
        url = url_map.get(row.media_id) or url_map.get(str(row.media_id))
        if url:
            item.file_url = url
            item.thumbnail_url = url
        out.append(item)
    return out


async def _load_submission_members(
    submission_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeSubmissionMemberResponse]:
    """Return per-member roster rows with full names hydrated.

    Names come from a single bulk lookup against the local Member table —
    admin-facing UIs render member names rather than raw UUIDs.
    """
    rows = await db.execute(
        select(ChallengeSubmissionMember)
        .where(ChallengeSubmissionMember.submission_id == submission_id)
        .order_by(ChallengeSubmissionMember.created_at)
    )
    items = list(rows.scalars().all())
    if not items:
        return []

    name_map = await _load_member_names([m.member_id for m in items], db)
    out: List[ChallengeSubmissionMemberResponse] = []
    for row in items:
        item = ChallengeSubmissionMemberResponse.model_validate(row)
        item.member_name = name_map.get(row.member_id)
        out.append(item)
    return out


async def _load_member_records(member_ids: List[uuid.UUID], db: AsyncSession) -> dict:
    """Bulk-load member name records by id (single in-service DB query).

    Returns dict[UUID -> (first_name, last_name)]. The cross-service
    equivalent — `libs.common.service_client.get_members_bulk` —
    forwards to `/internal/members/bulk` over HTTP and is intended for
    OTHER services calling members_service. Inside members_service we
    query the local Member table directly to skip the HTTP hop.

    Callers pick the formatting they need via _full_name / _short_name.
    """
    unique = list({mid for mid in member_ids if mid is not None})
    if not unique:
        return {}
    rows = await db.execute(
        select(Member.id, Member.first_name, Member.last_name).where(
            Member.id.in_(unique)
        )
    )
    return {row.id: (row.first_name, row.last_name) for row in rows.all()}


def _full_name(record: Optional[tuple]) -> Optional[str]:
    """Format a (first, last) tuple as "First Last". None if record missing."""
    if not record:
        return None
    first = (record[0] or "").strip()
    last = (record[1] or "").strip()
    full = f"{first} {last}".strip()
    return full or None


async def _load_member_names(member_ids: List[uuid.UUID], db: AsyncSession) -> dict:
    """Convenience: bulk-resolve member ids → "First Last" strings.

    Thin wrapper around _load_member_records + _full_name; kept so the
    admin-facing call sites stay readable.
    """
    records = await _load_member_records(member_ids, db)
    return {mid: _full_name(rec) for mid, rec in records.items() if _full_name(rec)}


async def _hydrate_challenge_response(
    challenge: ClubChallenge, db: AsyncSession
) -> ClubChallengeResponse:
    """Build a ClubChallengeResponse with example media + counts."""
    import json

    # Approved-only count (the "completion_count" semantics in the original
    # API were "all rows"; we preserve that as submission_count and add a
    # stricter completion_count = approved-only for UI clarity).
    approved_count = (
        await db.execute(
            select(func.count(MemberChallengeCompletion.id)).where(
                MemberChallengeCompletion.challenge_id == challenge.id,
                MemberChallengeCompletion.status == "approved",
            )
        )
    ).scalar_one()

    submission_count = (
        await db.execute(
            select(func.count(MemberChallengeCompletion.id)).where(
                MemberChallengeCompletion.challenge_id == challenge.id
            )
        )
    ).scalar_one()

    example_media = await _load_challenge_example_media(challenge.id, db)

    # Resolve the badge artwork URL once so both admin and member-facing
    # list/detail views can render the actual badge instead of falling back
    # to a generic Trophy icon. Single bulk HTTP call to media_service.
    badge_image_url: Optional[str] = None
    if challenge.reward_badge_image_media_id is not None:
        url_map = await resolve_media_urls([challenge.reward_badge_image_media_id])
        badge_image_url = url_map.get(
            challenge.reward_badge_image_media_id
        ) or url_map.get(str(challenge.reward_badge_image_media_id))

    challenge_dict = {
        column.name: getattr(challenge, column.name)
        for column in challenge.__table__.columns
    }
    challenge_dict["criteria_json"] = (
        json.loads(challenge.criteria_json) if challenge.criteria_json else None
    )
    challenge_dict["completion_count"] = approved_count
    challenge_dict["submission_count"] = submission_count
    challenge_dict["example_media"] = example_media
    challenge_dict["badge_image_url"] = badge_image_url

    return ClubChallengeResponse.model_validate(challenge_dict)


async def _hydrate_submission_response(
    submission: MemberChallengeCompletion, db: AsyncSession
) -> ChallengeSubmissionResponse:
    """Build the rich submission response used by both the legacy and new
    review surfaces. Hydrates proof media, per-member roster (with names),
    and the captain's display name + parent challenge title."""
    import json

    sub_dict = {
        column.name: getattr(submission, column.name)
        for column in submission.__table__.columns
    }
    sub_dict["result_data"] = (
        json.loads(submission.result_data) if submission.result_data else None
    )
    sub_dict["proof_media"] = await _load_submission_proof_media(submission.id, db)
    sub_dict["members"] = await _load_submission_members(submission.id, db)

    # Captain name (top-level convenience for the admin queue table)
    captain_name_map = await _load_member_names([submission.member_id], db)
    sub_dict["member_name"] = captain_name_map.get(submission.member_id)

    # Parent challenge title (avoids a round-trip per row in the queue UI)
    title_row = await db.execute(
        select(ClubChallenge.title).where(ClubChallenge.id == submission.challenge_id)
    )
    title = title_row.scalar_one_or_none()
    sub_dict["challenge_title"] = title

    return ChallengeSubmissionResponse.model_validate(sub_dict)


# ---------------------------------------------------------------------------
# Public surface helpers
# ---------------------------------------------------------------------------


def _short_display_name(record: Optional[tuple]) -> str:
    """Render a privacy-friendly public display name: "First L." form.

    Used on the public landing page to identify a winner without leaking
    the full surname. Accepts a (first_name, last_name) tuple from
    _load_member_records.
    """
    if not record:
        return "Anonymous"
    first = (record[0] or "").strip()
    last = (record[1] or "").strip()
    if not first and not last:
        return "Anonymous"
    if not last:
        return first
    return f"{first} {last[0]}.".strip()


async def _build_winner_info(
    challenge: ClubChallenge, db: AsyncSession
) -> Optional[ChallengeWinnerPublicInfo]:
    """Resolve the public winner block for a competition-format challenge.

    Returns None if the challenge has no winner_submission_id, or if the
    referenced submission no longer exists / is no longer approved.

    Privacy guarantees:
      * Names are short-form ("Tobi A.") — no full surname, no email.
      * Proof media is included ONLY when challenge.show_winner_media_publicly
        is true; otherwise the public sees the winner's name with no media.
    """
    if challenge.winner_submission_id is None:
        return None

    sub_row = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.id == challenge.winner_submission_id,
            MemberChallengeCompletion.status == "approved",
        )
    )
    submission = sub_row.scalar_one_or_none()
    if not submission:
        return None

    members_rows = await db.execute(
        select(ChallengeSubmissionMember).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    sub_members = list(members_rows.scalars().all())

    member_ids = (
        [m.member_id for m in sub_members] if sub_members else [submission.member_id]
    )

    records = await _load_member_records(member_ids, db)
    name_map = {mid: _short_display_name(rec) for mid, rec in records.items()}

    captain_member_id = submission.submitted_by_member_id or submission.member_id
    captain_name = name_map.get(captain_member_id, "Anonymous")
    teammate_names = [
        name_map[mid]
        for mid in member_ids
        if mid != captain_member_id and mid in name_map
    ]

    proof_media: List[ChallengeSubmissionMediaResponse] = []
    if challenge.show_winner_media_publicly:
        proof_media = await _load_submission_proof_media(submission.id, db)

    return ChallengeWinnerPublicInfo(
        submission_id=submission.id,
        captain_name=captain_name,
        teammate_names=teammate_names,
        is_team_submission=submission.is_team_submission,
        proof_media=proof_media,
    )


async def _hydrate_public_challenge_response(
    challenge: ClubChallenge,
    db: AsyncSession,
    *,
    include_winner: bool,
) -> ChallengePublicResponse:
    """Build a public-safe response.

    `include_winner=False` is used by the list endpoint (cheap; skips the
    extra DB queries for winner roster + proof media). The detail endpoint
    passes True to populate the winner block.
    """
    approved_count = (
        await db.execute(
            select(func.count(MemberChallengeCompletion.id)).where(
                MemberChallengeCompletion.challenge_id == challenge.id,
                MemberChallengeCompletion.status == "approved",
            )
        )
    ).scalar_one()

    example_media = await _load_challenge_example_media(challenge.id, db)

    badge_image_url: Optional[str] = None
    if challenge.reward_badge_image_media_id is not None:
        url_map = await resolve_media_urls([challenge.reward_badge_image_media_id])
        badge_image_url = url_map.get(
            challenge.reward_badge_image_media_id
        ) or url_map.get(str(challenge.reward_badge_image_media_id))

    now = utc_now()
    is_finished = challenge.ends_at is not None and challenge.ends_at < now

    winner_info: Optional[ChallengeWinnerPublicInfo] = None
    if include_winner and challenge.format == "competition":
        winner_info = await _build_winner_info(challenge, db)

    return ChallengePublicResponse(
        id=challenge.id,
        title=challenge.title,
        description=challenge.description,
        instructions=challenge.instructions,
        challenge_type=challenge.challenge_type,
        badge_name=challenge.badge_name,
        reward_badge_image_media_id=challenge.reward_badge_image_media_id,
        badge_image_url=badge_image_url,
        reward_bubbles_amount=challenge.reward_bubbles_amount,
        reward_volunteer_hours=(
            float(challenge.reward_volunteer_hours)
            if challenge.reward_volunteer_hours is not None
            else None
        ),
        audience=challenge.audience,
        format=challenge.format,
        starts_at=challenge.starts_at,
        ends_at=challenge.ends_at,
        team_enabled=challenge.team_enabled,
        team_min_size=challenge.team_min_size,
        team_max_size=challenge.team_max_size,
        completion_count=approved_count,
        example_media=example_media,
        winner=winner_info,
        is_finished=is_finished,
        series_slug=challenge.series_slug,
        series_order=challenge.series_order,
        created_at=challenge.created_at,
    )


async def _award_badge_and_members(
    submission: MemberChallengeCompletion,
    challenge: ClubChallenge,
    db: AsyncSession,
) -> None:
    """On approval (local-only writes): badge ledger + per-member roster.

    Cross-service Bubbles/volunteer-hours grants are fired by
    `_distribute_external_rewards` AFTER the local transaction commits —
    that pattern (commit local first, then external grants best-effort)
    matches the pools_service approval flow and keeps the approval
    succeeding even if wallet/volunteer services are temporarily down.
    """
    members_rows = await db.execute(
        select(ChallengeSubmissionMember).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    members = list(members_rows.scalars().all())

    # Solo submissions may have no submission_members row (legacy admin
    # mark-complete path). Treat the submission's member_id as the lone
    # recipient in that case.
    target_member_ids = (
        [m.member_id for m in members] if members else [submission.member_id]
    )

    for target_id in target_member_ids:
        existing = await db.execute(
            select(ChallengeBadgeAward).where(
                ChallengeBadgeAward.member_id == target_id,
                ChallengeBadgeAward.challenge_id == challenge.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        db.add(
            ChallengeBadgeAward(
                member_id=target_id,
                challenge_id=challenge.id,
                submission_id=submission.id,
                badge_name=challenge.badge_name,
                badge_image_media_id=challenge.reward_badge_image_media_id,
            )
        )

    # Mark per-member rows as badge-awarded. The bubbles_grant_id and
    # volunteer_hours_log_id columns are populated separately by
    # _distribute_external_rewards after the local commit.
    for m in members:
        m.badge_awarded = True


async def _notify_submission_reviewed(
    submission: MemberChallengeCompletion,
    challenge: ClubChallenge,
    db: AsyncSession,
    *,
    status: str,
    review_note: Optional[str],
) -> None:
    """Send an in-app notification to every member on the submission roster.

    Best-effort — `dispatch_notification` swallows its own errors. Approve
    notifications mention the badge + any extra rewards; reject
    notifications include the admin's review_note when present so the
    member knows what to fix before re-submitting.
    """
    members_rows = await db.execute(
        select(ChallengeSubmissionMember.member_id).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    member_ids = [str(row[0]) for row in members_rows.all()]
    if not member_ids:
        # Solo legacy path may not have a roster row; fall back to the
        # submission's primary member.
        member_ids = [str(submission.member_id)]

    action_url = f"/community/challenges/{challenge.id}"

    if status == "approved":
        reward_bits: List[str] = [f"🏅 {challenge.badge_name}"]
        if challenge.reward_bubbles_amount:
            reward_bits.append(f"💧 {challenge.reward_bubbles_amount} Bubbles")
        if challenge.reward_volunteer_hours:
            reward_bits.append(
                f"⏱ {challenge.reward_volunteer_hours} volunteer hour"
                f"{'s' if float(challenge.reward_volunteer_hours) != 1 else ''}"
            )
        body = (
            f"Your attempt at \"{challenge.title}\" was approved. "
            f"Earned: {' · '.join(reward_bits)}."
        )
        await dispatch_notification(
            type="challenge_submission_approved",
            category="challenges",
            member_ids=member_ids,
            title=f"Challenge approved: {challenge.title}",
            body=body,
            action_url=action_url,
            icon="trophy",
            calling_service=CHALLENGES_CALLING_SERVICE,
            metadata={
                "challenge_id": str(challenge.id),
                "submission_id": str(submission.id),
                "badge_name": challenge.badge_name,
            },
        )
    elif status == "rejected":
        body = f'Your attempt at "{challenge.title}" wasn\'t approved this time.'
        if review_note:
            body += f" Note from the reviewer: {review_note}"
        body += " You can try again any time."
        await dispatch_notification(
            type="challenge_submission_rejected",
            category="challenges",
            member_ids=member_ids,
            title=f"Challenge attempt — please retry: {challenge.title}",
            body=body,
            action_url=action_url,
            icon="rotate-ccw",
            calling_service=CHALLENGES_CALLING_SERVICE,
            metadata={
                "challenge_id": str(challenge.id),
                "submission_id": str(submission.id),
            },
        )


async def _notify_submission_winner(
    submission: MemberChallengeCompletion,
    challenge: ClubChallenge,
    db: AsyncSession,
) -> None:
    """Notify every member on the winning submission that they won.

    Best-effort, fire-and-forget. Includes the challenge title + a deep
    link to the public detail page so the member can show off the win.
    """
    members_rows = await db.execute(
        select(ChallengeSubmissionMember.member_id).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    member_ids = [str(row[0]) for row in members_rows.all()]
    if not member_ids:
        member_ids = [str(submission.member_id)]

    body = (
        f'Your attempt at "{challenge.title}" was selected as the winner. '
        "Congrats — your name is up on the public challenge page."
    )
    await dispatch_notification(
        type="challenge_winner_selected",
        category="challenges",
        member_ids=member_ids,
        title=f"You won: {challenge.title}",
        body=body,
        action_url=f"/challenges/{challenge.id}",
        icon="trophy",
        calling_service=CHALLENGES_CALLING_SERVICE,
        metadata={
            "challenge_id": str(challenge.id),
            "submission_id": str(submission.id),
        },
    )


async def _distribute_external_rewards(
    submission: MemberChallengeCompletion,
    challenge: ClubChallenge,
    db: AsyncSession,
    *,
    granted_by_auth: Optional[str],
) -> None:
    """Cross-service grants: Bubbles via wallet_service, hours via volunteer_service.

    Best-effort; the local approval has already committed. Per-member
    failures are logged and leave the corresponding ledger column null so
    a future re-trigger (e.g. reapproving the same submission) can
    succeed without double-granting (idempotency is enforced by
    wallet's campaign_code and volunteer's external_reference_id unique
    index).
    """
    if (
        challenge.reward_bubbles_amount is None
        and challenge.reward_volunteer_hours is None
    ):
        return

    members_rows = await db.execute(
        select(ChallengeSubmissionMember).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    members = list(members_rows.scalars().all())
    if not members:
        # Legacy mark-complete path: synthesize a roster from the
        # submission row so the loop below grants to the lone member.
        # We don't write the row to the DB here — only use it to drive
        # external calls; if the legacy path needs ledger tracking too,
        # mark_challenge_complete writes the join row before this runs.
        members = [
            ChallengeSubmissionMember(
                submission_id=submission.id,
                member_id=submission.member_id,
            )
        ]

    # Resolve auth_ids in bulk for the wallet call (which keys by Supabase
    # auth_id, not the local Member.id).
    member_ids = [m.member_id for m in members]
    auth_rows = await db.execute(
        select(Member.id, Member.auth_id).where(Member.id.in_(member_ids))
    )
    auth_id_map = {row.id: row.auth_id for row in auth_rows.all()}

    submission_id_str = str(submission.id)
    bubbles_amount = challenge.reward_bubbles_amount
    hours = (
        float(challenge.reward_volunteer_hours)
        if challenge.reward_volunteer_hours is not None
        else None
    )

    for m in members:
        member_id_str = str(m.member_id)

        # ---- Bubbles ---------------------------------------------------
        if bubbles_amount is not None and m.bubbles_grant_id is None:
            auth_id = auth_id_map.get(m.member_id)
            if auth_id:
                try:
                    grant = await grant_challenge_reward_bubbles(
                        member_auth_id=auth_id,
                        bubbles_amount=bubbles_amount,
                        submission_id=submission_id_str,
                        member_id=member_id_str,
                        granted_by=granted_by_auth or "admin",
                        calling_service=CHALLENGES_CALLING_SERVICE,
                    )
                    grant_id_raw = grant.get("id")
                    if grant_id_raw:
                        try:
                            m.bubbles_grant_id = uuid.UUID(str(grant_id_raw))
                        except (ValueError, TypeError):
                            logger.warning(
                                "wallet returned non-UUID grant id %r for submission %s",
                                grant_id_raw,
                                submission.id,
                            )
                except Exception as exc:
                    logger.warning(
                        "Bubbles grant failed for submission %s member %s: %s",
                        submission.id,
                        member_id_str,
                        exc,
                    )
            else:
                logger.warning(
                    "No auth_id found for member %s — skipping Bubbles grant for submission %s",
                    member_id_str,
                    submission.id,
                )

        # ---- Volunteer hours ------------------------------------------
        if hours is not None and m.volunteer_hours_log_id is None:
            try:
                log = await grant_challenge_volunteer_hours(
                    member_id=member_id_str,
                    hours=hours,
                    submission_id=submission_id_str,
                    logged_by=granted_by_auth,
                    notes=f"Challenge: {challenge.title}",
                    calling_service=CHALLENGES_CALLING_SERVICE,
                )
                log_id_raw = log.get("log_id")
                if log_id_raw:
                    try:
                        m.volunteer_hours_log_id = uuid.UUID(str(log_id_raw))
                    except (ValueError, TypeError):
                        logger.warning(
                            "volunteer returned non-UUID log id %r for submission %s",
                            log_id_raw,
                            submission.id,
                        )
            except Exception as exc:
                logger.warning(
                    "Volunteer hours grant failed for submission %s member %s: %s",
                    submission.id,
                    member_id_str,
                    exc,
                )

        if m.bubbles_grant_id is not None or m.volunteer_hours_log_id is not None:
            m.rewarded_at = utc_now()
