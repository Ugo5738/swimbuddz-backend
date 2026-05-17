"""Admin handbook-version CRUD endpoints."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.members_service.models import HandbookVersion, Member
from services.members_service.schemas import (
    CreateHandbookVersionRequest,
    HandbookVersionDetail,
    HandbookVersionListItem,
)
from sqlalchemy import select

logger = get_logger(__name__)
router = APIRouter()


@router.get("/handbook/versions", response_model=list[HandbookVersionListItem])
async def list_handbook_versions(
    current_user: AuthUser = Depends(require_admin),
):
    """List all handbook versions (admin)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(HandbookVersion).order_by(HandbookVersion.created_at.desc())
        )
        versions = result.scalars().all()

        return [
            HandbookVersionListItem(
                id=str(v.id),
                version=v.version,
                title=v.title,
                effective_date=v.effective_date,
                is_current=v.is_current,
                content_hash=v.content_hash,
                created_at=v.created_at,
            )
            for v in versions
        ]


@router.post("/handbook", response_model=HandbookVersionDetail)
async def create_handbook_version(
    data: CreateHandbookVersionRequest,
    current_user: AuthUser = Depends(require_admin),
):
    """Create a new handbook version (admin). Deactivates previous current version."""
    async with AsyncSessionLocal() as session:
        # Check if version already exists
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.version == data.version)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Handbook version {data.version} already exists.",
            )

        # Deactivate current version
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.is_current.is_(True))
        )
        current = result.scalar_one_or_none()
        if current:
            current.is_current = False

        # Get admin member ID
        admin_result = await session.execute(
            select(Member).where(Member.auth_id == current_user.user_id)
        )
        admin_member = admin_result.scalar_one_or_none()

        content_hash = hashlib.sha256(data.content.encode("utf-8")).hexdigest()

        handbook = HandbookVersion(
            id=uuid.uuid4(),
            version=data.version,
            title=data.title,
            content=data.content,
            content_hash=content_hash,
            effective_date=data.effective_date,
            is_current=True,
            created_by_id=admin_member.id if admin_member else None,
        )
        session.add(handbook)
        await session.commit()
        await session.refresh(handbook)

        logger.info(
            f"Created handbook version {data.version}",
            extra={"extra_fields": {"version": data.version}},
        )

        return HandbookVersionDetail(
            id=str(handbook.id),
            version=handbook.version,
            title=handbook.title,
            content=handbook.content,
            content_hash=handbook.content_hash,
            effective_date=handbook.effective_date,
            is_current=handbook.is_current,
            created_by_id=(
                str(handbook.created_by_id) if handbook.created_by_id else None
            ),
            created_at=handbook.created_at,
            updated_at=handbook.updated_at,
        )
