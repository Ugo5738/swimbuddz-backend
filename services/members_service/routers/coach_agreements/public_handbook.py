"""Coach-facing handbook endpoints (current + version lookup)."""

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.config import AsyncSessionLocal
from services.members_service.models import HandbookVersion
from services.members_service.schemas import HandbookContentResponse
from sqlalchemy import select

from ._shared import _strip_internal_handbook_sections

router = APIRouter()


@router.get("/handbook/current", response_model=HandbookContentResponse)
async def get_current_handbook(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current handbook content for display to coaches."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.is_current.is_(True))
        )
        handbook = result.scalar_one_or_none()

        if not handbook:
            raise HTTPException(
                status_code=404,
                detail="No current handbook version found.",
            )

        return HandbookContentResponse(
            version=handbook.version,
            title=handbook.title,
            content=_strip_internal_handbook_sections(handbook.content),
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
        )


@router.get("/handbook/{version}", response_model=HandbookContentResponse)
async def get_handbook_version(
    version: str,
    current_user: AuthUser = Depends(get_current_user),
):
    """Get a specific handbook version."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.version == version)
        )
        handbook = result.scalar_one_or_none()

        if not handbook:
            raise HTTPException(
                status_code=404,
                detail=f"Handbook version {version} not found.",
            )

        return HandbookContentResponse(
            version=handbook.version,
            title=handbook.title,
            content=_strip_internal_handbook_sections(handbook.content),
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
        )
