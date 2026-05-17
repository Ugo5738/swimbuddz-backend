"""Public (no-auth) challenge listing + detail endpoints.

Powers the unauthenticated landing-page tiles + winner reveal. Paths
sit under `/challenges/public/*` (three-segment paths with a literal
"public" middle) so they never pattern-collide with the parameterised
`/challenges/{challenge_id}` routes.
"""

import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.members_service.models import ClubChallenge
from services.members_service.schemas import ChallengePublicResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import _hydrate_public_challenge_response

router = APIRouter()


@router.get("/public/all", response_model=List[ChallengePublicResponse])
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


@router.get("/public/{challenge_id}", response_model=ChallengePublicResponse)
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
