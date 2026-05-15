"""Member-facing submission endpoints (list my submissions + create new)."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.service_client import dispatch_notification
from libs.db.session import get_async_db
from services.members_service.models import (
    ChallengeSubmissionMedia,
    ChallengeSubmissionMember,
    ClubChallenge,
    MemberChallengeCompletion,
)
from services.members_service.schemas import (
    ChallengeSubmissionCreate,
    ChallengeSubmissionResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import (
    CHALLENGES_CALLING_SERVICE,
    _enforce_prerequisite,
    _full_name,
    _hydrate_submission_response,
    _load_member_records,
    _resolve_member_id_from_auth,
)

router = APIRouter()


@router.get(
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


@router.post(
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

    # Hard-gating: if requires_challenge_id is set on this challenge, ALL
    # team members (including captain) must already have an approved badge
    # for the prerequisite. Soft progression (no requires_challenge_id) is
    # the default — admins opt INTO gating per challenge.
    if challenge.requires_challenge_id is not None:
        team_ids_to_check = [captain_member_id, *submission_data.team_member_ids]
        await _enforce_prerequisite(
            db,
            prerequisite_id=challenge.requires_challenge_id,
            member_ids=team_ids_to_check,
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
