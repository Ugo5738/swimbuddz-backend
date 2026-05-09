"""Router for volunteer (legacy) and club challenge management.

The challenges section was reshaped in Phase 1:
  * Challenges carry example media via a join table (challenge_example_media)
    and gain audience / format / scoping / rewards / team config columns.
  * Submissions live in member_challenge_completions but are first-class
    with a pending → approved | rejected lifecycle. Multiple attempts per
    (member, challenge) are allowed; prior attempts are preserved.
  * Approval writes a row to challenge_badge_awards plus per-member rows
    in challenge_submission_members. Cross-service reward distribution
    (Bubbles + volunteer hours) lands in Phase 7.
"""

import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.service_client import (
    dispatch_notification,
    grant_challenge_reward_bubbles,
    grant_challenge_volunteer_hours,
)
from libs.db.session import get_async_db

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)
from services.members_service.models import (
    ChallengeBadgeAward,
    ChallengeExampleMedia,
    ChallengeSubmissionMedia,
    ChallengeSubmissionMember,
    ClubChallenge,
    Member,
    MemberChallengeCompletion,
    VolunteerInterest,
    VolunteerRole,
)
from services.members_service.schemas import (
    ChallengeCompletionCreate,
    ChallengeCompletionResponse,
    ChallengeExampleMediaResponse,
    ChallengePublicResponse,
    ChallengeSubmissionCreate,
    ChallengeSubmissionMediaResponse,
    ChallengeSubmissionMemberResponse,
    ChallengeSubmissionResponse,
    ChallengeSubmissionReview,
    ChallengeWinnerPublicInfo,
    ClubChallengeCreate,
    ClubChallengeResponse,
    ClubChallengeUpdate,
    VolunteerInterestCreate,
    VolunteerInterestResponse,
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# ===== VOLUNTEER ROLE ROUTER =====
volunteer_router = APIRouter(prefix="/volunteers", tags=["volunteers"])


@volunteer_router.get("/roles", response_model=List[VolunteerRoleResponse])
async def list_volunteer_roles(
    active_only: bool = Query(True, description="Show only active roles"),
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer roles with optional filters."""
    query = select(VolunteerRole)

    if active_only:
        query = query.where(VolunteerRole.is_active.is_(True))

    query = query.order_by(VolunteerRole.created_at.desc())

    result = await db.execute(query)
    roles = result.scalars().all()

    # Get interested member counts for each role
    roles_with_counts = []
    for role in roles:
        interest_query = select(func.count(VolunteerInterest.id)).where(
            VolunteerInterest.role_id == role.id
        )
        interest_result = await db.execute(interest_query)
        interested_count = interest_result.scalar_one()

        role_dict = role.__dict__.copy()
        role_dict["interested_count"] = interested_count
        roles_with_counts.append(VolunteerRoleResponse.model_validate(role_dict))

    return roles_with_counts


@volunteer_router.get("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def get_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single volunteer role by ID."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.post("/roles", response_model=VolunteerRoleResponse, status_code=201)
async def create_volunteer_role(
    role_data: VolunteerRoleCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Create a new volunteer role (admin only)."""
    role = VolunteerRole(**role_data.model_dump())

    db.add(role)
    await db.commit()
    await db.refresh(role)

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = 0

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.patch("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def update_volunteer_role(
    role_id: uuid.UUID,
    role_data: VolunteerRoleUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Update a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Update only provided fields
    for field, value in role_data.model_dump(exclude_unset=True).items():
        setattr(role, field, value)

    await db.commit()
    await db.refresh(role)

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.delete("/roles/{role_id}", status_code=204)
async def delete_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Delete a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Delete associated interests first
    await db.execute(
        select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)
    )
    await db.delete(role)
    await db.commit()

    return None


# ===== VOLUNTEER INTEREST ENDPOINTS =====
@volunteer_router.post(
    "/interest", response_model=VolunteerInterestResponse, status_code=201
)
async def register_volunteer_interest(
    interest_data: VolunteerInterestCreate,
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Register interest in a volunteer role (legacy; admin-only).

    NOTE: VolunteerRole/VolunteerInterest tables are LEGACY (renamed to
    legacy_volunteer_*) and the active volunteer programme lives in
    volunteer_service. This endpoint is gated to admin so the legacy
    surface cannot be exercised anonymously while the legacy data lingers.
    """
    # Check if role exists
    role_query = select(VolunteerRole).where(VolunteerRole.id == interest_data.role_id)
    role_result = await db.execute(role_query)
    role = role_result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Check if already interested
    existing_query = select(VolunteerInterest).where(
        VolunteerInterest.role_id == interest_data.role_id,
        VolunteerInterest.member_id == member_id,
    )
    existing_result = await db.execute(existing_query)
    existing_interest = existing_result.scalar_one_or_none()

    if existing_interest:
        raise HTTPException(
            status_code=400, detail="Already registered interest in this role"
        )

    interest = VolunteerInterest(
        role_id=interest_data.role_id, member_id=member_id, notes=interest_data.notes
    )

    db.add(interest)
    await db.commit()
    await db.refresh(interest)

    return VolunteerInterestResponse.model_validate(interest)


@volunteer_router.get(
    "/roles/{role_id}/interested", response_model=List[VolunteerInterestResponse]
)
async def list_interested_members(
    role_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List members interested in a volunteer role (admin only)."""
    query = select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)

    if status:
        query = query.where(VolunteerInterest.status == status)

    result = await db.execute(query)
    interests = result.scalars().all()

    return [
        VolunteerInterestResponse.model_validate(interest) for interest in interests
    ]


# ===== CLUB CHALLENGE ROUTER =====
challenge_router = APIRouter(prefix="/challenges", tags=["challenges"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Public surface (no auth)
#
# Sub-paths under /challenges/public/* — three-segment paths with a literal
# "public" middle so they can never pattern-collide with the parameterised
# /challenges/{challenge_id} routes below. No auth required: this is what
# powers the unauthenticated landing-page tiles + winner reveal.
# ---------------------------------------------------------------------------


@challenge_router.get("/public/all", response_model=List[ChallengePublicResponse])
async def list_public_challenges(
    status_filter: Optional[Literal["active", "finished", "all"]] = Query(
        None,
        alias="status",
        description="Filter by lifecycle status. 'active' = currently running; "
        "'finished' = ends_at in the past; omit or 'all' = both.",
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """List public-visible challenges for anonymous viewers.

    Excludes admin-internal fields (criteria_json, club_id, academy_cohort_id,
    is_active, is_public). Sorted: active first by start date asc, then
    finished by end date desc.
    """
    now = utc_now()
    query = select(ClubChallenge).where(
        ClubChallenge.is_public.is_(True),
        ClubChallenge.is_active.is_(True),
    )

    if status_filter == "active":
        # Active = (no start, or start is past) AND (no end, or end is future)
        query = query.where(
            (ClubChallenge.starts_at.is_(None)) | (ClubChallenge.starts_at <= now)
        ).where((ClubChallenge.ends_at.is_(None)) | (ClubChallenge.ends_at > now))
    elif status_filter == "finished":
        query = query.where(ClubChallenge.ends_at < now)

    # Sort: still-running first (earlier end-dates first so urgency floats),
    # then finished by most-recent end. NULLS-last semantics handled by the
    # DB; we do a Python sort below as a clean fallback.
    rows = await db.execute(query)
    challenges = list(rows.scalars().all())

    def sort_key(c: ClubChallenge):
        is_finished = c.ends_at is not None and c.ends_at < now
        # Bucket 0 = running, 1 = finished. Within bucket: ends_at ascending
        # for running, descending for finished.
        if is_finished:
            return (1, -(c.ends_at.timestamp() if c.ends_at else 0))
        return (0, c.ends_at.timestamp() if c.ends_at else 0)

    challenges.sort(key=sort_key)

    return [
        await _hydrate_public_challenge_response(c, db, include_winner=False)
        for c in challenges
    ]


@challenge_router.get("/public/{challenge_id}", response_model=ChallengePublicResponse)
async def get_public_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Public detail for a single challenge — includes winner info.

    Returns 404 for any challenge with is_public=False even if it exists,
    so private challenges aren't enumerable from the outside.
    """
    row = await db.execute(
        select(ClubChallenge).where(
            ClubChallenge.id == challenge_id,
            ClubChallenge.is_public.is_(True),
        )
    )
    challenge = row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return await _hydrate_public_challenge_response(challenge, db, include_winner=True)


# ---------------------------------------------------------------------------
# Challenge CRUD
# ---------------------------------------------------------------------------


@challenge_router.get("/", response_model=List[ClubChallengeResponse])
async def list_club_challenges(
    active_only: bool = Query(True, description="Show only active challenges"),
    challenge_type: Optional[str] = Query(None, description="Filter by challenge type"),
    audience: Optional[str] = Query(None, description="Filter by audience"),
    db: AsyncSession = Depends(get_async_db),
):
    """List club challenges with optional filters."""
    query = select(ClubChallenge)

    if active_only:
        query = query.where(ClubChallenge.is_active.is_(True))
    if challenge_type:
        query = query.where(ClubChallenge.challenge_type == challenge_type)
    if audience:
        query = query.where(ClubChallenge.audience == audience)

    query = query.order_by(ClubChallenge.created_at.desc())

    result = await db.execute(query)
    challenges = result.scalars().all()

    return [await _hydrate_challenge_response(c, db) for c in challenges]


@challenge_router.get("/{challenge_id}", response_model=ClubChallengeResponse)
async def get_club_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single club challenge by ID."""
    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    return await _hydrate_challenge_response(challenge, db)


@challenge_router.post("/", response_model=ClubChallengeResponse, status_code=201)
async def create_club_challenge(
    challenge_data: ClubChallengeCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Create a new club challenge (admin only)."""
    import json

    payload = challenge_data.model_dump(exclude={"criteria_json", "example_media"})
    challenge = ClubChallenge(
        **payload,
        criteria_json=(
            json.dumps(challenge_data.criteria_json)
            if challenge_data.criteria_json
            else None
        ),
    )
    db.add(challenge)
    await db.flush()  # populate challenge.id without committing yet

    for item in challenge_data.example_media:
        db.add(
            ChallengeExampleMedia(
                challenge_id=challenge.id,
                media_id=item.media_id,
                order_idx=item.order_idx,
                caption=item.caption,
            )
        )

    await db.commit()
    await db.refresh(challenge)

    return await _hydrate_challenge_response(challenge, db)


@challenge_router.patch("/{challenge_id}", response_model=ClubChallengeResponse)
async def update_club_challenge(
    challenge_id: uuid.UUID,
    challenge_data: ClubChallengeUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Update a club challenge (admin only).

    If example_media is supplied, the entire example-media set is replaced
    (delete-and-reinsert in one transaction). If omitted, existing example
    media is left untouched.
    """
    import json

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    update_data = challenge_data.model_dump(
        exclude_unset=True, exclude={"criteria_json", "example_media"}
    )
    for field, value in update_data.items():
        setattr(challenge, field, value)

    explicit = challenge_data.model_dump(exclude_unset=True)
    if "criteria_json" in explicit:
        challenge.criteria_json = (
            json.dumps(challenge_data.criteria_json)
            if challenge_data.criteria_json
            else None
        )

    if challenge_data.example_media is not None:
        await db.execute(
            delete(ChallengeExampleMedia).where(
                ChallengeExampleMedia.challenge_id == challenge.id
            )
        )
        for item in challenge_data.example_media:
            db.add(
                ChallengeExampleMedia(
                    challenge_id=challenge.id,
                    media_id=item.media_id,
                    order_idx=item.order_idx,
                    caption=item.caption,
                )
            )

    await db.commit()
    await db.refresh(challenge)

    return await _hydrate_challenge_response(challenge, db)


@challenge_router.delete("/{challenge_id}", status_code=204)
async def delete_club_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Delete a club challenge (admin only).

    Example media + submissions + submission media + submission members
    cascade via FK ON DELETE CASCADE. Badge awards reference the submission
    via SET NULL so historical badges survive challenge deletion.
    """
    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    await db.delete(challenge)
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


@challenge_router.get(
    "/submissions/mine", response_model=List[ChallengeSubmissionResponse]
)
async def list_my_submissions(
    challenge_id: Optional[uuid.UUID] = Query(
        None,
        description="Filter to a single challenge (e.g. for the member detail "
        "page's 'your past attempts' panel).",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """Return every submission the authenticated member has made.

    Powers two member-facing surfaces:
      * "Your status" pill on the challenges list tile (newest submission
        per challenge → derive a current pending|approved|rejected chip).
      * "Past attempts" list on the challenge detail page, filtered to a
        single challenge_id.

    The result includes team submissions where the member is on the roster
    (not just where they're the captain) — so a teammate sees a shared
    submission even though they didn't initiate it.
    """
    member_id = await _resolve_member_id_from_auth(current_user, db)

    # A member's submissions = (a) ones they created (member_id==me), OR
    # (b) ones they're on the roster of (challenge_submission_members).
    # Use a UNION on submission ids to dedupe.
    own_q = select(MemberChallengeCompletion.id).where(
        MemberChallengeCompletion.member_id == member_id
    )
    team_q = select(ChallengeSubmissionMember.submission_id).where(
        ChallengeSubmissionMember.member_id == member_id
    )
    if challenge_id:
        own_q = own_q.where(MemberChallengeCompletion.challenge_id == challenge_id)
        team_q = team_q.join(
            MemberChallengeCompletion,
            MemberChallengeCompletion.id == ChallengeSubmissionMember.submission_id,
        ).where(MemberChallengeCompletion.challenge_id == challenge_id)

    id_rows = await db.execute(own_q.union(team_q))
    submission_ids = [row[0] for row in id_rows.all()]
    if not submission_ids:
        return []

    rows = await db.execute(
        select(MemberChallengeCompletion)
        .where(MemberChallengeCompletion.id.in_(submission_ids))
        .order_by(MemberChallengeCompletion.created_at.desc())
    )
    submissions = list(rows.scalars().all())
    return [await _hydrate_submission_response(s, db) for s in submissions]


@challenge_router.post(
    "/{challenge_id}/submissions",
    response_model=ChallengeSubmissionResponse,
    status_code=201,
)
async def create_challenge_submission(
    challenge_id: uuid.UUID,
    submission_data: ChallengeSubmissionCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: AuthUser = Depends(get_current_user),
):
    """Submit an attempt at a challenge (member-driven).

    Captain (current authenticated member) is added to the team roster
    automatically. team_member_ids contains the OTHER teammates.

    Members are blocked from creating a new submission while they have a
    pending or already-approved submission on the same challenge — they
    can re-submit only after rejection. Prior submission rows are never
    deleted; they're preserved for audit.
    """
    import json

    # Verify challenge exists and is active
    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")
    if not challenge.is_active:
        raise HTTPException(status_code=400, detail="Challenge is not active")

    # Resolve captain (the submitter) from auth
    captain_member_id = await _resolve_member_id_from_auth(current_user, db)

    # Ignore any client-sent challenge_id mismatch — the path param wins
    if submission_data.challenge_id and submission_data.challenge_id != challenge_id:
        raise HTTPException(
            status_code=400, detail="challenge_id in body does not match path"
        )

    # Block re-submit while a pending or approved submission exists
    blocking = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.challenge_id == challenge_id,
            MemberChallengeCompletion.member_id == captain_member_id,
            MemberChallengeCompletion.status.in_(("pending", "approved")),
        )
    )
    if blocking.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400,
            detail="You already have a pending or approved submission for this challenge",
        )

    # Team validation
    is_team = bool(submission_data.team_member_ids)
    if is_team and not challenge.team_enabled:
        raise HTTPException(
            status_code=400, detail="This challenge does not allow team submissions"
        )
    if is_team:
        team_size = 1 + len(submission_data.team_member_ids)  # captain + others
        if challenge.team_min_size and team_size < challenge.team_min_size:
            raise HTTPException(
                status_code=400,
                detail=f"Team has {team_size} members, minimum is {challenge.team_min_size}",
            )
        if challenge.team_max_size and team_size > challenge.team_max_size:
            raise HTTPException(
                status_code=400,
                detail=f"Team has {team_size} members, maximum is {challenge.team_max_size}",
            )
        # Reject duplicates including captain
        all_ids = {captain_member_id, *submission_data.team_member_ids}
        if len(all_ids) != team_size:
            raise HTTPException(
                status_code=400, detail="Duplicate member in team roster"
            )

    submission = MemberChallengeCompletion(
        challenge_id=challenge_id,
        member_id=captain_member_id,
        submitted_by_member_id=captain_member_id,
        submission_note=submission_data.submission_note,
        is_team_submission=is_team,
        status="pending",
        result_data=(
            json.dumps(submission_data.result_data)
            if submission_data.result_data
            else None
        ),
    )
    db.add(submission)
    await db.flush()

    # Captain row first so it sorts to the top
    db.add(
        ChallengeSubmissionMember(
            submission_id=submission.id,
            member_id=captain_member_id,
            role="captain" if is_team else None,
        )
    )
    for teammate_id in submission_data.team_member_ids:
        db.add(
            ChallengeSubmissionMember(
                submission_id=submission.id,
                member_id=teammate_id,
            )
        )

    for idx, media in enumerate(submission_data.proof_media):
        db.add(
            ChallengeSubmissionMedia(
                submission_id=submission.id,
                media_id=media.media_id,
                order_idx=media.order_idx if media.order_idx else idx,
            )
        )

    await db.commit()
    await db.refresh(submission)

    # Best-effort: ping every teammate (excluding the captain) so they know
    # they were added to a team submission. Fire-and-forget; never blocks.
    if is_team and submission_data.team_member_ids:
        captain_records = await _load_member_records([captain_member_id], db)
        captain_name = (
            _full_name(captain_records.get(captain_member_id)) or "A teammate"
        )
        await dispatch_notification(
            type="challenge_team_invite",
            category="challenges",
            member_ids=[str(mid) for mid in submission_data.team_member_ids],
            title=f"You're on a team for: {challenge.title}",
            body=(
                f"{captain_name} added you to a team submission. The admin "
                "will review the attempt — you'll be notified when it's "
                "approved."
            ),
            action_url=f"/community/challenges/{challenge.id}",
            icon="users",
            calling_service=CHALLENGES_CALLING_SERVICE,
            metadata={
                "challenge_id": str(challenge.id),
                "submission_id": str(submission.id),
                "captain_member_id": str(captain_member_id),
            },
        )

    return await _hydrate_submission_response(submission, db)


@challenge_router.get(
    "/submissions/pending", response_model=List[ChallengeSubmissionResponse]
)
async def list_pending_submissions_legacy(
    challenge_id: Optional[uuid.UUID] = Query(
        None, description="Filter by challenge (optional)"
    ),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """LEGACY alias for /submissions/list?status=pending.

    Kept so any existing frontend bindings keep working through a deploy.
    Prefer GET /challenges/submissions/list with the `status` query param.
    """
    return await _list_submissions_impl(
        status_filter="pending",
        challenge_id=challenge_id,
        db=db,
    )


@challenge_router.get(
    "/submissions/list", response_model=List[ChallengeSubmissionResponse]
)
async def list_submissions(
    status: Literal["pending", "approved", "rejected", "all"] = Query(
        "pending",
        description="Filter by submission status. 'all' returns every "
        "submission across statuses, ordered newest first.",
    ),
    challenge_id: Optional[uuid.UUID] = Query(
        None, description="Filter by challenge (optional)"
    ),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Admin review queue: list submissions filtered by status.

    Powers the approved/rejected tabs in the admin review UI in addition to
    the default pending bucket.
    """
    return await _list_submissions_impl(
        status_filter=status,
        challenge_id=challenge_id,
        db=db,
    )


async def _list_submissions_impl(
    *,
    status_filter: str,
    challenge_id: Optional[uuid.UUID],
    db: AsyncSession,
) -> List[ChallengeSubmissionResponse]:
    """Shared implementation for the legacy `/submissions/pending` route
    and the new `/submissions/list?status=` route. Order:
      - approved  → newest reviewed_at first (most-recently approved)
      - rejected  → newest reviewed_at first
      - pending   → oldest created_at first (FIFO queue feel)
      - all       → newest created_at first
    """
    query = select(MemberChallengeCompletion)
    if status_filter != "all":
        query = query.where(MemberChallengeCompletion.status == status_filter)
    if challenge_id:
        query = query.where(MemberChallengeCompletion.challenge_id == challenge_id)

    if status_filter == "pending":
        query = query.order_by(MemberChallengeCompletion.created_at.asc())
    elif status_filter in ("approved", "rejected"):
        query = query.order_by(
            MemberChallengeCompletion.reviewed_at.desc().nulls_last(),
            MemberChallengeCompletion.created_at.desc(),
        )
    else:
        query = query.order_by(MemberChallengeCompletion.created_at.desc())

    rows = await db.execute(query)
    submissions = list(rows.scalars().all())
    return [await _hydrate_submission_response(s, db) for s in submissions]


@challenge_router.patch(
    "/submissions/{submission_id}", response_model=ChallengeSubmissionResponse
)
async def review_challenge_submission(
    submission_id: uuid.UUID,
    review: ChallengeSubmissionReview,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """Approve or reject a submission (admin only).

    Approve: writes a badge award per member (idempotent via unique
    (member_id, challenge_id) constraint). Bubbles + volunteer-hours
    distribution lands in Phase 7.

    Re-approving an already-approved submission is a no-op (idempotent).
    A rejected submission can be re-approved to fix a mistaken rejection;
    a previously-approved submission can be rejected to revoke (the badge
    award row remains — revocation logic, if needed, lands later).
    """
    submission_row = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.id == submission_id
        )
    )
    submission = submission_row.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == submission.challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        # Should not happen given FK + soft constraints; defensive 404.
        raise HTTPException(status_code=404, detail="Linked challenge not found")

    submission.status = review.status
    submission.review_note = review.review_note
    submission.reviewed_at = utc_now()
    submission.reviewed_by = _admin_uuid_or_none(admin)

    if review.status == "approved":
        await _award_badge_and_members(submission, challenge, db)
        # Mark this round of approval; per-member grant ids fill in below
        if submission.rewards_distributed_at is None:
            submission.rewards_distributed_at = utc_now()

    # First commit — persists the local approval (status, badge ledger,
    # roster). The approval succeeds even if the cross-service grants
    # below fail; that's deliberate.
    await db.commit()
    await db.refresh(submission)

    # Best-effort cross-service reward distribution AFTER the commit. Each
    # per-member call is idempotent (campaign_code on wallet,
    # external_reference_id on volunteer), so re-approving the same
    # submission after a transient failure won't double-grant.
    if review.status == "approved":
        await _distribute_external_rewards(
            submission,
            challenge,
            db,
            granted_by_auth=admin.user_id,
        )
        # Persist any grant ids returned during distribution.
        await db.commit()
        await db.refresh(submission)

    # Member-facing notification (fire-and-forget; never blocks the response).
    await _notify_submission_reviewed(
        submission,
        challenge,
        db,
        status=review.status,
        review_note=review.review_note,
    )

    return await _hydrate_submission_response(submission, db)


@challenge_router.post(
    "/submissions/{submission_id}/mark-winner",
    response_model=ChallengeSubmissionResponse,
)
async def mark_submission_as_winner(
    submission_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """Mark an approved submission as the winner of its challenge.

    Side effects:
      * Sets ClubChallenge.winner_submission_id to this submission.
      * Sends an in-app notification to every member on the submission
        roster ("Congrats — you won {challenge}!").

    Constraints:
      * Submission must be approved (otherwise 400).
      * Challenge must be format='competition' (otherwise 400).
      * Re-marking the same submission as winner is a no-op for the FK
        update but still re-fires the notification (admins occasionally
        want to re-announce); rev-marking to a different submission is
        allowed.
    """
    submission_row = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.id == submission_id
        )
    )
    submission = submission_row.scalar_one_or_none()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    if submission.status != "approved":
        raise HTTPException(
            status_code=400,
            detail="Submission must be approved before it can be marked the winner.",
        )

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == submission.challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Linked challenge not found")
    if challenge.format != "competition":
        raise HTTPException(
            status_code=400,
            detail="Only competition-format challenges have a winner.",
        )

    challenge.winner_submission_id = submission.id
    await db.commit()
    await db.refresh(challenge)

    # Notify every member on the winning submission's roster.
    await _notify_submission_winner(submission, challenge, db)

    return await _hydrate_submission_response(submission, db)


# ---------------------------------------------------------------------------
# Legacy completions endpoints (kept for back-compat with the admin form)
# ---------------------------------------------------------------------------


@challenge_router.post(
    "/completions", response_model=ChallengeCompletionResponse, status_code=201
)
async def mark_challenge_complete(
    completion_data: ChallengeCompletionCreate,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """LEGACY: admin records a pre-approved completion for a member.

    Kept so existing admin tooling continues to work. The new submission
    flow lives at POST /challenges/{id}/submissions + PATCH /submissions/{id}.

    This path creates a submission already in `approved` status with the
    admin recorded as the reviewer, writes a per-member roster row, and
    produces a badge award. Multiple "approved" rows for the same member
    on the same challenge are blocked here (use a fresh challenge instead).
    """
    import json

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == completion_data.challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Block creating a duplicate approved completion (the badge ledger
    # would dedupe anyway, but giving a clear 400 keeps clients honest).
    existing = await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.challenge_id == completion_data.challenge_id,
            MemberChallengeCompletion.member_id == completion_data.member_id,
            MemberChallengeCompletion.status == "approved",
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=400, detail="Challenge already approved for this member"
        )

    admin_uuid = _admin_uuid_or_none(admin)

    completion = MemberChallengeCompletion(
        challenge_id=completion_data.challenge_id,
        member_id=completion_data.member_id,
        submitted_by_member_id=completion_data.member_id,
        result_data=(
            json.dumps(completion_data.result_data)
            if completion_data.result_data
            else None
        ),
        status="approved",
        verified_by=admin_uuid,
        reviewed_by=admin_uuid,
        reviewed_at=utc_now(),
    )
    db.add(completion)
    await db.flush()

    db.add(
        ChallengeSubmissionMember(
            submission_id=completion.id,
            member_id=completion_data.member_id,
        )
    )

    await _award_badge_and_members(completion, challenge, db)
    completion.rewards_distributed_at = utc_now()

    # First commit — persists the local approval before firing
    # cross-service grants (same pattern as review_challenge_submission).
    await db.commit()
    await db.refresh(completion)

    # Best-effort cross-service Bubbles + volunteer-hours grants. Failures
    # are logged; the approval still succeeds.
    await _distribute_external_rewards(
        completion,
        challenge,
        db,
        granted_by_auth=admin.user_id,
    )
    await db.commit()
    await db.refresh(completion)

    # Member-facing notification (fire-and-forget).
    await _notify_submission_reviewed(
        completion,
        challenge,
        db,
        status="approved",
        review_note=None,
    )

    completion_dict = {
        column.name: getattr(completion, column.name)
        for column in completion.__table__.columns
    }
    completion_dict["result_data"] = (
        json.loads(completion.result_data) if completion.result_data else None
    )
    return ChallengeCompletionResponse.model_validate(completion_dict)


@challenge_router.get(
    "/{challenge_id}/completions", response_model=List[ChallengeCompletionResponse]
)
async def list_challenge_completions(
    challenge_id: uuid.UUID,
    status: Optional[str] = Query(
        None, description="Filter by status: pending|approved|rejected"
    ),
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    """List submissions for a specific challenge (admin only).

    Returns the legacy ChallengeCompletionResponse shape for back-compat.
    For the richer per-member + media view, use /submissions/pending or
    a future GET /challenges/{id}/submissions endpoint.
    """
    import json

    query = select(MemberChallengeCompletion).where(
        MemberChallengeCompletion.challenge_id == challenge_id
    )
    if status:
        query = query.where(MemberChallengeCompletion.status == status)
    query = query.order_by(MemberChallengeCompletion.completed_at.desc())

    result = await db.execute(query)
    completions = result.scalars().all()

    completions_list = []
    for completion in completions:
        completion_dict = {
            column.name: getattr(completion, column.name)
            for column in completion.__table__.columns
        }
        completion_dict["result_data"] = (
            json.loads(completion.result_data) if completion.result_data else None
        )
        completions_list.append(
            ChallengeCompletionResponse.model_validate(completion_dict)
        )

    return completions_list
