"""Admin agreement-version CRUD endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.members_service.models import (
    AgreementVersion,
    CoachAgreement,
    CoachProfile,
    Member,
)
from services.members_service.schemas import (
    AgreementVersionDetail,
    AgreementVersionListItem,
    CreateAgreementVersionRequest,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ._shared import _compute_agreement_hash

logger = get_logger(__name__)
settings = get_settings()
router = APIRouter()


@router.get("/agreements", response_model=list[AgreementVersionListItem])
async def list_agreement_versions(
    _admin: dict = Depends(require_admin),
):
    """List all agreement versions with signature counts (admin only)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgreementVersion).order_by(AgreementVersion.created_at.desc())
        )
        versions = result.scalars().all()

        items = []
        for v in versions:
            # Count signatures for this version
            sig_result = await session.execute(
                select(func.count(CoachAgreement.id)).where(
                    CoachAgreement.agreement_version == v.version
                )
            )
            sig_count = sig_result.scalar() or 0

            items.append(
                AgreementVersionListItem(
                    id=str(v.id),
                    version=v.version,
                    title=v.title,
                    effective_date=v.effective_date,
                    is_current=v.is_current,
                    content_hash=v.content_hash,
                    signature_count=sig_count,
                    created_at=v.created_at,
                )
            )
        return items


@router.post("/agreements", response_model=AgreementVersionDetail)
async def create_agreement_version(
    data: CreateAgreementVersionRequest,
    admin: AuthUser = Depends(require_admin),
):
    """Create a new agreement version (admin only).

    Auto-sets the new version as current and deactivates the previous one.
    Sends email notification to all active coaches about the new version.
    """
    admin_id = admin.user_id
    content_hash = _compute_agreement_hash(data.content)

    async with AsyncSessionLocal() as session:
        # Check version doesn't already exist
        existing = await session.execute(
            select(AgreementVersion).where(AgreementVersion.version == data.version)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Version {data.version} already exists",
            )

        # Deactivate all current versions
        result = await session.execute(
            select(AgreementVersion).where(AgreementVersion.is_current.is_(True))
        )
        for old_version in result.scalars().all():
            old_version.is_current = False

        # Create new version
        new_version = AgreementVersion(
            version=data.version,
            title=data.title,
            content=data.content,
            content_hash=content_hash,
            effective_date=data.effective_date,
            is_current=True,
            created_by_id=admin_id,
        )
        session.add(new_version)
        await session.commit()
        await session.refresh(new_version)

        # Send notification emails to active coaches (non-blocking)
        try:
            coach_result = await session.execute(
                select(CoachProfile)
                .join(Member)
                .options(selectinload(CoachProfile.member))
                .where(CoachProfile.status == "active")
            )
            active_coaches = coach_result.scalars().all()

            if active_coaches:
                coach_emails = [
                    c.member.email
                    for c in active_coaches
                    if c.member and c.member.email
                ]
                if coach_emails:
                    frontend_base = settings.FRONTEND_URL.rstrip("/")
                    agreement_link = f"{frontend_base}/coach/agreement"

                    email_client = get_email_client()
                    await email_client.send_bulk(
                        to_emails=coach_emails,
                        subject=f"New Coach Agreement Version {data.version} — Signature Required",
                        body=(
                            f"Hi Coach,\n\n"
                            f"A new version ({data.version}) of the SwimBuddz Coach Agreement is now available.\n\n"
                            f"Please review and sign the updated agreement at your earliest convenience:\n"
                            f"{agreement_link}\n\n"
                            f"Until you sign the new agreement, some dashboard features may be restricted.\n\n"
                            f"Best regards,\n"
                            f"The SwimBuddz Team"
                        ),
                    )
                    logger.info(
                        f"Sent agreement update notification to {len(coach_emails)} coaches",
                        extra={"extra_fields": {"version": data.version}},
                    )
        except Exception as e:
            # Email failure should not block agreement creation
            logger.error(
                f"Failed to send agreement update notifications: {e}",
                extra={"extra_fields": {"version": data.version}},
            )

        return AgreementVersionDetail(
            id=str(new_version.id),
            version=new_version.version,
            title=new_version.title,
            content=new_version.content,
            content_hash=new_version.content_hash,
            effective_date=new_version.effective_date,
            is_current=new_version.is_current,
            created_by_id=(
                str(new_version.created_by_id) if new_version.created_by_id else None
            ),
            signature_count=0,
            active_signature_count=0,
            created_at=new_version.created_at,
            updated_at=new_version.updated_at,
        )


@router.get("/agreements/{version_id}", response_model=AgreementVersionDetail)
async def get_agreement_version_detail(
    version_id: str,
    _admin: dict = Depends(require_admin),
):
    """Get a specific agreement version with signature statistics."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgreementVersion).where(AgreementVersion.id == version_id)
        )
        version = result.scalar_one_or_none()
        if not version:
            raise HTTPException(status_code=404, detail="Agreement version not found")

        # Count total signatures for this version
        sig_result = await session.execute(
            select(func.count(CoachAgreement.id)).where(
                CoachAgreement.agreement_version == version.version
            )
        )
        total_sigs = sig_result.scalar() or 0

        # Count active signatures (coaches currently on this version)
        active_sig_result = await session.execute(
            select(func.count(CoachAgreement.id)).where(
                CoachAgreement.agreement_version == version.version,
                CoachAgreement.is_active.is_(True),
            )
        )
        active_sigs = active_sig_result.scalar() or 0

        return AgreementVersionDetail(
            id=str(version.id),
            version=version.version,
            title=version.title,
            content=version.content,
            content_hash=version.content_hash,
            effective_date=version.effective_date,
            is_current=version.is_current,
            created_by_id=str(version.created_by_id) if version.created_by_id else None,
            signature_count=total_sigs,
            active_signature_count=active_sigs,
            created_at=version.created_at,
            updated_at=version.updated_at,
        )
