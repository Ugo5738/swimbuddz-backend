"""Post-approval reward side effects.

Local-only: badge-ledger writes + per-member roster rows on approval.
Cross-service: Bubbles grants via wallet_service, volunteer hours via
volunteer_service. Both helpers are best-effort tolerant — failures
are logged and surfaced in payment_metadata, not raised.
"""

import uuid
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    grant_challenge_reward_bubbles,
    grant_challenge_volunteer_hours,
)
from services.members_service.models import (
    ChallengeBadgeAward,
    ChallengeSubmissionMember,
    ClubChallenge,
    Member,
    MemberChallengeCompletion,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)


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
