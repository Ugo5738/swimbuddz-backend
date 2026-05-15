"""In-app notification dispatch for submission lifecycle events.

Fire-and-forget calls to communications_service after a review action.
Failures never block the review.
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
