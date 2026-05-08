"""Club CRUD router.

  * GET /clubs            — public list (no auth) for picker autocomplete
  * GET /clubs/{id}       — public single record
  * POST /clubs           — admin only
  * PATCH /clubs/{id}     — admin only
  * DELETE /clubs/{id}    — admin only

Soft-FK relationships: club_id is referenced from club_challenges (and
potentially other tables in the future) without a hard FK, so deletion
just removes the row — challenges that pointed to it keep their stale
club_id but the picker filters them out by virtue of the club no longer
existing.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Club
from services.members_service.schemas import (
    ClubCreate,
    ClubResponse,
    ClubUpdate,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/clubs", tags=["clubs"])


@router.get("/", response_model=List[ClubResponse])
async def list_clubs(
    active_only: bool = Query(
        True, description="Hide inactive clubs (default true)."
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """List clubs. Public — used by the challenges admin form picker and
    any future club-scoped landing pages."""
    query = select(Club)
    if active_only:
        query = query.where(Club.is_active.is_(True))
    query = query.order_by(Club.name.asc())
    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/{club_id}", response_model=ClubResponse)
async def get_club(
    club_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    return club


@router.post("/", response_model=ClubResponse, status_code=201)
async def create_club(
    body: ClubCreate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    club = Club(**body.model_dump())
    db.add(club)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Slug already taken — pick something unique.",
        ) from exc
    await db.refresh(club)
    return club


@router.patch("/{club_id}", response_model=ClubResponse)
async def update_club(
    club_id: uuid.UUID,
    body: ClubUpdate,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club: Optional[Club] = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(club, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Slug already taken — pick something unique.",
        ) from exc
    await db.refresh(club)
    return club


@router.delete("/{club_id}", status_code=204)
async def delete_club(
    club_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
    _admin: AuthUser = Depends(require_admin),
):
    row = await db.execute(select(Club).where(Club.id == club_id))
    club: Optional[Club] = row.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    await db.delete(club)
    await db.commit()
    return None
