"""Club-challenge CRUD + series listing.

`/series/list` (GET, static 2-seg) must register before
`/{challenge_id}` (GET, 1-seg catch-all) so FastAPI doesn't capture
"series" as a UUID. Both live in this file and the route order below
preserves that invariant.
"""

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import ChallengeExampleMedia, ClubChallenge
from services.members_service.schemas import (
    ClubChallengeCreate,
    ClubChallengeResponse,
    ClubChallengeUpdate,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import _hydrate_challenge_response

router = APIRouter()


@router.get("/", response_model=List[ClubChallengeResponse])
async def list_club_challenges(
    active_only: bool = Query(True, description="Show only active challenges"),
    challenge_type: Optional[str] = Query(None, description="Filter by challenge type"),
    audience: Optional[str] = Query(None, description="Filter by audience"),
    series_slug: Optional[str] = Query(
        None,
        description=(
            "Filter by skill-ladder series. Pass an exact slug to fetch one "
            "ladder; pass the literal value 'none' to fetch only standalone "
            "(non-ladder) challenges."
        ),
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """List club challenges with optional filters.

    Skill-ladder behaviour:
      * series_slug=<slug>  → returns just that ladder's steps, ordered
      * series_slug='none'  → returns only standalone challenges
      * series_slug omitted → returns everything (admin/list view)
    """
    query = select(ClubChallenge)

    if active_only:
        query = query.where(ClubChallenge.is_active.is_(True))
    if challenge_type:
        query = query.where(ClubChallenge.challenge_type == challenge_type)
    if audience:
        query = query.where(ClubChallenge.audience == audience)
    if series_slug == "none":
        query = query.where(ClubChallenge.series_slug.is_(None))
    elif series_slug:
        query = query.where(ClubChallenge.series_slug == series_slug)

    # Within a single series, order by series_order ascending so the
    # ladder reads top-to-bottom. Otherwise newest-first (admin queue feel).
    if series_slug and series_slug != "none":
        query = query.order_by(
            ClubChallenge.series_order.asc().nulls_last(),
            ClubChallenge.created_at.asc(),
        )
    else:
        query = query.order_by(ClubChallenge.created_at.desc())

    result = await db.execute(query)
    challenges = result.scalars().all()

    return [await _hydrate_challenge_response(c, db) for c in challenges]


@router.get(
    "/series/list",
    response_model=Dict[str, List[ClubChallengeResponse]],
)
async def list_challenges_by_series(
    audience: Optional[str] = Query(
        None,
        description=(
            "Filter to a single audience tier (e.g. 'club') so a tier-page "
            "shows only the relevant ladders."
        ),
    ),
    active_only: bool = Query(True, description="Hide inactive challenges"),
    db: AsyncSession = Depends(get_async_db),
):
    """Return all challenges that belong to a series, grouped by slug.

    Powers the Club page's "skill ladders showcase". Standalone (no
    `series_slug`) challenges are excluded — they show on the homepage
    carousel instead. Within each series, steps are ordered by
    `series_order`.
    """
    query = select(ClubChallenge).where(ClubChallenge.series_slug.is_not(None))
    if active_only:
        query = query.where(ClubChallenge.is_active.is_(True))
    if audience:
        query = query.where(ClubChallenge.audience == audience)
    query = query.order_by(
        ClubChallenge.series_slug.asc(),
        ClubChallenge.series_order.asc().nulls_last(),
        ClubChallenge.created_at.asc(),
    )

    rows = await db.execute(query)
    challenges = rows.scalars().all()

    out: Dict[str, List[ClubChallengeResponse]] = {}
    for c in challenges:
        slug = c.series_slug or "_unknown"
        out.setdefault(slug, []).append(await _hydrate_challenge_response(c, db))
    return out


@router.get("/{challenge_id}", response_model=ClubChallengeResponse)
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


@router.post("/", response_model=ClubChallengeResponse, status_code=201)
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


@router.patch("/{challenge_id}", response_model=ClubChallengeResponse)
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


@router.delete("/{challenge_id}", status_code=204)
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
