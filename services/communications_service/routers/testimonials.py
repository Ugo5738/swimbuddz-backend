"""Testimonial bank — public list endpoint + admin CRUD.

Public endpoint returns only published testimonials, optionally filtered
by track (academy / club / community / all). Admin endpoints require
an authenticated admin user and expose the full bank.
"""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.communications_service.models import Testimonial

router = APIRouter(tags=["testimonials"])


VALID_TRACKS = {"academy", "club", "community", "all"}


# ── Schemas ────────────────────────────────────────────────────────────


class TestimonialResponse(BaseModel):
    id: uuid.UUID
    author_name: str
    author_role: str
    author_since: Optional[str] = None
    author_initials: str
    author_photo_url: Optional[str] = None
    quote: str
    tracks: List[str]
    is_published: bool
    sort_order: int

    class Config:
        from_attributes = True


class TestimonialCreate(BaseModel):
    author_name: str = Field(..., min_length=1, max_length=120)
    author_role: str = Field(..., min_length=1, max_length=120)
    author_since: Optional[str] = Field(None, max_length=20)
    author_initials: str = Field(..., min_length=1, max_length=4)
    author_photo_url: Optional[str] = None
    quote: str = Field(..., min_length=10)
    tracks: List[str] = Field(default_factory=list)
    is_published: bool = False
    sort_order: int = 100
    consent_note: Optional[str] = None


class TestimonialUpdate(BaseModel):
    author_name: Optional[str] = Field(None, min_length=1, max_length=120)
    author_role: Optional[str] = Field(None, min_length=1, max_length=120)
    author_since: Optional[str] = Field(None, max_length=20)
    author_initials: Optional[str] = Field(None, min_length=1, max_length=4)
    author_photo_url: Optional[str] = None
    quote: Optional[str] = Field(None, min_length=10)
    tracks: Optional[List[str]] = None
    is_published: Optional[bool] = None
    sort_order: Optional[int] = None
    consent_note: Optional[str] = None


def _validate_tracks(tracks: List[str]) -> None:
    bad = [t for t in tracks if t not in VALID_TRACKS]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid track(s): {bad}. " f"Valid options: {sorted(VALID_TRACKS)}."
            ),
        )


# ── Public endpoint ────────────────────────────────────────────────────


@router.get("/testimonials/public", response_model=List[TestimonialResponse])
async def list_public_testimonials(
    track: Optional[str] = Query(
        None,
        description="Filter by track: academy|club|community|all. Omit for all tracks.",
    ),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    """Return published testimonials, optionally filtered by track."""
    if track is not None and track not in VALID_TRACKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid track '{track}'. Valid: {sorted(VALID_TRACKS)}.",
        )

    query = (
        select(Testimonial)
        .where(Testimonial.is_published.is_(True))
        .order_by(Testimonial.sort_order.asc(), desc(Testimonial.created_at))
        .limit(limit)
    )
    result = await db.execute(query)
    items = list(result.scalars().all())

    if track is not None:
        # JSON array contains check done in Python (portable across dialects)
        items = [t for t in items if track in (t.tracks or [])]

    return items


# ── Admin endpoints ────────────────────────────────────────────────────


@router.get("/admin/testimonials", response_model=List[TestimonialResponse])
async def admin_list_testimonials(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin: list ALL testimonials, published or not."""
    query = select(Testimonial).order_by(
        Testimonial.sort_order.asc(), desc(Testimonial.created_at)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post(
    "/admin/testimonials",
    response_model=TestimonialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_testimonial(
    body: TestimonialCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    _validate_tracks(body.tracks)
    testimonial = Testimonial(**body.model_dump())
    db.add(testimonial)
    await db.commit()
    await db.refresh(testimonial)
    return testimonial


@router.patch(
    "/admin/testimonials/{testimonial_id}", response_model=TestimonialResponse
)
async def admin_update_testimonial(
    testimonial_id: uuid.UUID,
    body: TestimonialUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Testimonial).where(Testimonial.id == testimonial_id)
    )
    testimonial = result.scalar_one_or_none()
    if testimonial is None:
        raise HTTPException(status_code=404, detail="Testimonial not found")

    updates = body.model_dump(exclude_unset=True)
    if "tracks" in updates and updates["tracks"] is not None:
        _validate_tracks(updates["tracks"])

    for k, v in updates.items():
        setattr(testimonial, k, v)

    await db.commit()
    await db.refresh(testimonial)
    return testimonial


@router.delete(
    "/admin/testimonials/{testimonial_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def admin_delete_testimonial(
    testimonial_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    result = await db.execute(
        select(Testimonial).where(Testimonial.id == testimonial_id)
    )
    testimonial = result.scalar_one_or_none()
    if testimonial is None:
        raise HTTPException(status_code=404, detail="Testimonial not found")

    await db.delete(testimonial)
    await db.commit()
    return None
