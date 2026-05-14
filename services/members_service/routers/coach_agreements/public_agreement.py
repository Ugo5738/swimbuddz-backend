"""Coach-facing agreement endpoints (get / status / sign / history)."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.members_service.models import AgreementVersion, CoachAgreement, Member
from services.members_service.schemas import (
    AgreementContentResponse,
    CoachAgreementHistoryItem,
    CoachAgreementResponse,
    CoachAgreementStatusResponse,
    SignAgreementRequest,
    SignatureTypeEnum,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ._shared import _get_current_agreement_version, _render_agreement_for_coach

logger = get_logger(__name__)
router = APIRouter()


@router.get("/agreement/current", response_model=AgreementContentResponse)
async def get_current_agreement(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach agreement content, rendered with the coach's data.

    Placeholders like [COACH FULL NAME], [DATE], [Email Address] etc. are
    replaced with the authenticated coach's real data at fetch time.
    The content_hash still reflects the original template so that signing
    verification remains consistent across all coaches.
    """
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        current = await _get_current_agreement_version(session)

        # Fetch member + profile + coach profile for placeholder rendering
        result = await session.execute(
            select(Member)
            .options(
                selectinload(Member.profile),
                selectinload(Member.coach_profile),
            )
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if member and member.coach_profile:
            rendered_content = _render_agreement_for_coach(
                current.content, member, member.coach_profile
            )
        else:
            # Fallback: return raw template if no coach profile
            rendered_content = current.content

        return AgreementContentResponse(
            version=current.version,
            title=current.title,
            content=rendered_content,
            content_hash=current.content_hash,  # Hash of template, not rendered
            effective_date=current.effective_date,
            requires_signature=True,
        )


@router.get("/agreement/status", response_model=CoachAgreementStatusResponse)
async def get_agreement_status(
    current_user: AuthUser = Depends(get_current_user),
):
    """Check if the current coach has signed the latest agreement."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get current agreement version from DB
        current_av = await _get_current_agreement_version(session)
        current_version_str = current_av.version

        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Check for active agreement
        result = await session.execute(
            select(CoachAgreement)
            .where(
                CoachAgreement.coach_profile_id == coach.id,
                CoachAgreement.is_active.is_(True),
            )
            .order_by(CoachAgreement.signed_at.desc())
            .limit(1)
        )
        active_agreement = result.scalar_one_or_none()

        if not active_agreement:
            return CoachAgreementStatusResponse(
                has_signed_current_version=False,
                current_version=current_version_str,
                signed_version=None,
                signed_at=None,
                requires_new_signature=True,
            )

        # Check if signed version matches current
        has_signed_current = active_agreement.agreement_version == current_version_str

        return CoachAgreementStatusResponse(
            has_signed_current_version=has_signed_current,
            current_version=current_version_str,
            signed_version=active_agreement.agreement_version,
            signed_at=active_agreement.signed_at,
            requires_new_signature=not has_signed_current,
        )


@router.post("/agreement/sign", response_model=CoachAgreementResponse)
async def sign_agreement(
    data: SignAgreementRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """Sign the coach agreement."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Verify agreement version exists and is current
        result = await session.execute(
            select(AgreementVersion).where(
                AgreementVersion.is_current.is_(True),
                AgreementVersion.version == data.agreement_version,
            )
        )
        current_av = result.scalar_one_or_none()

        if not current_av:
            raise HTTPException(
                status_code=400,
                detail=f"Agreement version {data.agreement_version} is not the current version.",
            )

        if data.agreement_content_hash != current_av.content_hash:
            raise HTTPException(
                status_code=400,
                detail="Agreement content has changed. Please refresh and try again.",
            )

        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Mark any existing active agreements as superseded
        result = await session.execute(
            select(CoachAgreement).where(
                CoachAgreement.coach_profile_id == coach.id,
                CoachAgreement.is_active.is_(True),
            )
        )
        existing_agreements = result.scalars().all()

        # Validate handbook acknowledgment
        if not data.handbook_acknowledged:
            raise HTTPException(
                status_code=400,
                detail="You must acknowledge the Coach Handbook before signing the agreement.",
            )

        # Validate signature type specific requirements
        if (
            data.signature_type == SignatureTypeEnum.UPLOADED_IMAGE
            and not data.signature_media_id
        ):
            raise HTTPException(
                status_code=400,
                detail="signature_media_id is required when signature_type is uploaded_image.",
            )

        # For checkbox, auto-set signature_data
        sig_data = data.signature_data
        if data.signature_type == SignatureTypeEnum.CHECKBOX:
            sig_data = f"CHECKBOX_AGREE:{datetime.now(timezone.utc).isoformat()}"

        # Create new agreement
        new_agreement = CoachAgreement(
            coach_profile_id=coach.id,
            agreement_version=data.agreement_version,
            agreement_content_hash=data.agreement_content_hash,
            signature_type=data.signature_type.value,
            signature_data=sig_data,
            signature_media_id=(
                uuid.UUID(data.signature_media_id) if data.signature_media_id else None
            ),
            signed_at=datetime.now(timezone.utc),
            handbook_acknowledged=True,
            handbook_version=data.handbook_version,
            ip_address=None,  # Would get from request in production
            user_agent=None,  # Would get from request in production
            is_active=True,
        )
        session.add(new_agreement)

        # Supersede old agreements
        for old_agreement in existing_agreements:
            old_agreement.is_active = False
            old_agreement.superseded_by_id = new_agreement.id
            old_agreement.superseded_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(new_agreement)

        logger.info(
            f"Coach agreement signed: coach={coach.id}, version={data.agreement_version}",
            extra={
                "extra_fields": {
                    "coach_profile_id": str(coach.id),
                    "agreement_version": data.agreement_version,
                }
            },
        )

        return CoachAgreementResponse(
            id=str(new_agreement.id),
            coach_profile_id=str(new_agreement.coach_profile_id),
            agreement_version=new_agreement.agreement_version,
            signature_type=new_agreement.signature_type,
            signed_at=new_agreement.signed_at,
            is_active=new_agreement.is_active,
            ip_address=None,  # Don't expose full IP
            created_at=new_agreement.created_at,
        )


@router.get("/agreement/history", response_model=list[CoachAgreementHistoryItem])
async def get_agreement_history(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the coach's agreement signing history."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member and coach profile
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        member = result.scalar_one_or_none()

        if not member or not member.coach_profile:
            raise HTTPException(status_code=404, detail="No coach profile found")

        coach = member.coach_profile

        # Get all agreements
        result = await session.execute(
            select(CoachAgreement)
            .where(CoachAgreement.coach_profile_id == coach.id)
            .order_by(CoachAgreement.signed_at.desc())
        )
        agreements = result.scalars().all()

        return [
            CoachAgreementHistoryItem(
                id=str(a.id),
                agreement_version=a.agreement_version,
                signature_type=a.signature_type,
                signed_at=a.signed_at,
                is_active=a.is_active,
                superseded_at=a.superseded_at,
            )
            for a in agreements
        ]
