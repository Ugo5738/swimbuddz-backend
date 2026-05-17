"""Response builders.

Assemble the rich shapes returned by the various GET handlers:
ClubChallengeResponse (admin/club list), ChallengeSubmissionResponse
(submission + roster + proof media), ChallengePublicResponse
(privacy-trimmed public view), and the public winner block.
"""

from typing import List, Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from services.members_service.models import (
    ChallengeSubmissionMember,
    ClubChallenge,
    MemberChallengeCompletion,
)
from services.members_service.schemas import (
    ChallengePublicResponse,
    ChallengeSubmissionMediaResponse,
    ChallengeSubmissionResponse,
    ChallengeWinnerPublicInfo,
    ClubChallengeResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)

from ._media import (
    _load_challenge_example_media,
    _load_submission_members,
    _load_submission_proof_media,
)
from ._members import _load_member_names, _load_member_records, _short_display_name


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
