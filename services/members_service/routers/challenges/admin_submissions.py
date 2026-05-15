"""Admin / Pod-Lead submission review queue + actions."""

import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import (
    get_current_user,
    is_admin_or_service,
    require_admin,
)
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import dispatch_notification
from libs.db.session import get_async_db
from services.members_service.models import (
    ChallengeBadgeAward,
    ChallengeSubmissionMember,
    ClubChallenge,
    MemberChallengeCompletion,
    Pod,
    PodAssignment,
)
from services.members_service.schemas import (
    ChallengeSubmissionResponse,
    ChallengeSubmissionReview,
    ChallengeSubmissionRevokeRequest,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import (
    CHALLENGES_CALLING_SERVICE,
    _admin_uuid_or_none,
    _authorize_review,
    _award_badge_and_members,
    _distribute_external_rewards,
    _hydrate_submission_response,
    _notify_submission_reviewed,
    _notify_submission_winner,
    _resolve_member_id_from_auth_optional,
    logger,
)

router = APIRouter()


@router.get(
    "/submissions/pending", response_model=List[ChallengeSubmissionResponse]
)
async def list_pending_submissions_legacy(
    challenge_id: Optional[uuid.UUID] = Query(
        None, description="Filter by challenge (optional)"
    ),
    db: AsyncSession = Depends(get_async_db),
    reviewer: AuthUser = Depends(get_current_user),
):
    """LEGACY alias for /submissions/list?status=pending.

    Kept so any existing frontend bindings keep working through a deploy.
    Prefer GET /challenges/submissions/list with the `status` query param.
    """
    return await _list_submissions_impl(
        status_filter="pending",
        challenge_id=challenge_id,
        db=db,
        reviewer=reviewer,
    )


@router.get(
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
    reviewed_by_kind: Optional[
        Literal["admin", "pod_lead", "assistant_pod_lead"]
    ] = Query(
        None,
        description=(
            "Filter to submissions reviewed by a specific actor type. "
            "Powers the HQ audit page's 'just show Pod Lead approvals' view."
        ),
    ),
    revoked: Optional[Literal["only", "exclude"]] = Query(
        None,
        description=(
            "'only' = revoked submissions only. 'exclude' = hide revoked. "
            "Default (omitted) = include both. Use with status=approved + "
            "revoked=exclude on the audit page to see live approvals only."
        ),
    ),
    db: AsyncSession = Depends(get_async_db),
    reviewer: AuthUser = Depends(get_current_user),
):
    """Review queue — admin sees everything, Pod Leads see only their own
    pod's submissions.

    Powers the approved/rejected tabs in the admin review UI in addition
    to the default pending bucket. Pod Leads also use it via the same UI
    so they can clear their pod's queue independent of HQ.

    The `reviewed_by_kind` and `revoked` filters are intended for the HQ
    audit page; Pod Leads can pass them too but they only narrow within
    the per-pod scope they're already restricted to.
    """
    return await _list_submissions_impl(
        status_filter=status,
        challenge_id=challenge_id,
        reviewed_by_kind=reviewed_by_kind,
        revoked=revoked,
        db=db,
        reviewer=reviewer,
    )


async def _list_submissions_impl(
    *,
    status_filter: str,
    challenge_id: Optional[uuid.UUID],
    db: AsyncSession,
    reviewer: AuthUser,
    reviewed_by_kind: Optional[str] = None,
    revoked: Optional[str] = None,
) -> List[ChallengeSubmissionResponse]:
    """Shared implementation for the legacy `/submissions/pending` route
    and the new `/submissions/list?status=` route.

    Authorization:
      * Admin / service-role → see ALL submissions across the platform
      * Pod Lead / Assistant Pod Lead → see only submissions whose
        member is currently assigned to one of THEIR pods (any role).
        Excludes competition submissions because pod leads can't review
        those anyway, so showing them in their queue would just be noise.
      * Anyone else → 403 (the user has neither HQ admin nor pod-lead
        authority over any submission)

    Order:
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
    if reviewed_by_kind:
        query = query.where(
            MemberChallengeCompletion.reviewed_by_kind == reviewed_by_kind
        )
    if revoked == "only":
        query = query.where(MemberChallengeCompletion.revoked_at.is_not(None))
    elif revoked == "exclude":
        query = query.where(MemberChallengeCompletion.revoked_at.is_(None))

    if not is_admin_or_service(reviewer):
        # Pod-lead path: scope to submitters in pods this user leads.
        reviewer_member_id = await _resolve_member_id_from_auth_optional(reviewer, db)
        if reviewer_member_id is None:
            raise HTTPException(
                status_code=403,
                detail="Reviewer must be an admin or a Pod Lead.",
            )

        # Pods led by this reviewer (lead OR assistant lead)
        led_pods_q = select(Pod.id).where(
            (Pod.pod_lead_id == reviewer_member_id)
            | (Pod.assistant_pod_lead_id == reviewer_member_id)
        )
        led_pods_rows = await db.execute(led_pods_q)
        led_pod_ids = [row[0] for row in led_pods_rows.all()]
        if not led_pod_ids:
            raise HTTPException(
                status_code=403,
                detail=(
                    "You can only see submissions from members in a pod "
                    "you lead, but you don't lead any pods yet."
                ),
            )

        # Members currently assigned to one of those pods
        member_ids_subq = (
            select(PodAssignment.member_id)
            .where(
                PodAssignment.pod_id.in_(led_pod_ids),
                PodAssignment.left_at.is_(None),
            )
            .subquery()
        )
        query = query.where(
            MemberChallengeCompletion.member_id.in_(select(member_ids_subq))
        )

        # Exclude competitions — pod leads can't review them.
        competition_ids_subq = (
            select(ClubChallenge.id)
            .where(ClubChallenge.format == "competition")
            .subquery()
        )
        query = query.where(
            MemberChallengeCompletion.challenge_id.notin_(select(competition_ids_subq))
        )

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


@router.patch(
    "/submissions/{submission_id}", response_model=ChallengeSubmissionResponse
)
async def review_challenge_submission(
    submission_id: uuid.UUID,
    review: ChallengeSubmissionReview,
    db: AsyncSession = Depends(get_async_db),
    reviewer: AuthUser = Depends(get_current_user),
):
    """Approve or reject a submission (admin OR Pod Lead).

    Authorization (Phase 8b — delegated review):
      * SwimBuddz admin → can review anything
      * Pod Lead / Assistant Pod Lead → can review their own pod
        members' submissions, EXCEPT competition-format challenges
        (those stay HQ-only — too easy to game otherwise)

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

    # Authorize the reviewer; gets back which kind of authority they have
    # so we can stamp the audit trail. Raises 403 if neither path applies.
    reviewer_kind = await _authorize_review(
        reviewer=reviewer,
        challenge=challenge,
        submitter_member_id=submission.member_id,
        db=db,
    )

    submission.status = review.status
    submission.review_note = review.review_note
    submission.reviewed_at = utc_now()
    submission.reviewed_by = _admin_uuid_or_none(reviewer)
    submission.reviewed_by_kind = reviewer_kind

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
            granted_by_auth=reviewer.user_id,
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


@router.post(
    "/submissions/{submission_id}/revoke",
    response_model=ChallengeSubmissionResponse,
)
async def revoke_challenge_submission(
    submission_id: uuid.UUID,
    body: ChallengeSubmissionRevokeRequest,
    db: AsyncSession = Depends(get_async_db),
    admin: AuthUser = Depends(require_admin),
):
    """SwimBuddz HQ override — revoke a previously-approved submission.

    Used when HQ spot-checks a Pod Lead's approval (or one of their
    own legacy approvals) and finds it didn't actually meet the bar.
    Strictly admin-only; Pod Leads cannot revoke each other.

    Effects:
      * Stamps the submission with revoked_at / revoked_by / revoke_note
        (the original review fields stay intact for audit).
      * Stamps the corresponding challenge_badge_awards row with
        revoked_at so the badge stops showing on the member's profile,
        but the row itself is preserved for the audit trail.
      * Sends an in-app notification to every member on the submission's
        roster so they know what happened (and can re-attempt).

    What we DON'T do:
      * Reverse Bubbles or volunteer-hours grants. Those are external
        ledgers (wallet_service, volunteer_service); HQ should clawback
        manually via the wallet adjust UI if the situation warrants it.
        Doing it automatically here would invent a partial-refund flow
        that's not backed by the existing reward grants' idempotency keys.

    Idempotent on revoked_at: re-revoking just refreshes the note.
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
            detail=(
                "Only approved submissions can be revoked. Reject pending "
                "submissions instead."
            ),
        )

    challenge_row = await db.execute(
        select(ClubChallenge).where(ClubChallenge.id == submission.challenge_id)
    )
    challenge = challenge_row.scalar_one_or_none()
    if not challenge:
        raise HTTPException(status_code=404, detail="Linked challenge not found")

    now = utc_now()
    submission.revoked_at = now
    submission.revoked_by = _admin_uuid_or_none(admin)
    submission.revoke_note = body.revoke_note

    # Mark every badge award produced by this submission as revoked.
    # Awards may exist for a single member (solo sub) or every team
    # roster member (team sub) — _award_badge_and_members iterates the
    # roster on approval, so we mirror that here.
    members_rows = await db.execute(
        select(ChallengeSubmissionMember.member_id).where(
            ChallengeSubmissionMember.submission_id == submission.id
        )
    )
    target_member_ids = [row[0] for row in members_rows.all()]
    if not target_member_ids:
        target_member_ids = [submission.member_id]

    if target_member_ids:
        await db.execute(
            ChallengeBadgeAward.__table__.update()
            .where(
                ChallengeBadgeAward.challenge_id == challenge.id,
                ChallengeBadgeAward.member_id.in_(target_member_ids),
                ChallengeBadgeAward.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    await db.commit()
    await db.refresh(submission)

    # Member notification (best-effort) — make sure they hear it from us
    # before they spot the missing badge on their profile.
    try:
        await dispatch_notification(
            type="challenge_submission_revoked",
            category="challenges",
            member_ids=[str(mid) for mid in target_member_ids],
            title=f"Challenge approval revoked: {challenge.title}",
            body=(
                f'Your previous approval for "{challenge.title}" was '
                f"reviewed by SwimBuddz HQ and revoked. Reason: "
                f"{body.revoke_note} You can submit a new attempt anytime."
            ),
            action_url=f"/community/challenges/{challenge.id}",
            icon="alert-triangle",
            calling_service=CHALLENGES_CALLING_SERVICE,
            metadata={
                "challenge_id": str(challenge.id),
                "submission_id": str(submission.id),
            },
        )
    except Exception:
        # dispatch_notification already swallows; defensive belt + braces.
        logger.warning("challenge revoke notification failed", exc_info=True)

    return await _hydrate_submission_response(submission, db)


@router.post(
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
