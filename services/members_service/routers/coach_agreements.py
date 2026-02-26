"""Coach agreement and handbook routes."""

import hashlib
import re
import uuid
from datetime import date as date_type
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.members_service.models import (
    AgreementVersion,
    CoachAgreement,
    CoachProfile,
    HandbookVersion,
    Member,
)
from services.members_service.schemas import (
    AgreementContentResponse,
    AgreementVersionDetail,
    AgreementVersionListItem,
    CoachAgreementHistoryItem,
    CoachAgreementResponse,
    CoachAgreementStatusResponse,
    CreateAgreementVersionRequest,
    CreateHandbookVersionRequest,
    HandbookContentResponse,
    HandbookVersionDetail,
    HandbookVersionListItem,
    SignAgreementRequest,
    SignatureTypeEnum,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)

settings = get_settings()

router = APIRouter(prefix="/coaches", tags=["coaches"])
admin_router = APIRouter(prefix="/admin/coaches", tags=["admin-coaches"])


# ============================================================================
# SHARED HELPER FUNCTIONS
# ============================================================================


def _strip_internal_handbook_sections(content: str) -> str:
    """
    Coaches should not see internal-only appendices (e.g. Appendix B: system integration spec).
    Filter at the API boundary (defense in depth, even if the frontend also hides it).
    """
    m = re.search(r"^##\s+Appendix\s+B\b.*$", content, flags=re.MULTILINE)
    if not m:
        return content
    return content[: m.start()].rstrip() + "\n"


def _compute_agreement_hash(content: str) -> str:
    """Compute SHA-256 hash of agreement content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_agreement_for_coach(
    template_content: str,
    member: "Member",
    coach_profile: "CoachProfile",
) -> str:
    """Render agreement template by replacing placeholders with coach data.

    Placeholders replaced:
      [DATE]             -> today's date
      [COACH FULL NAME]  -> member first + last name
      [Coach Address]    -> from member profile (address, city, state)
      [Phone Number]     -> from member profile
      [Email Address]    -> from member record
      [PERCENTAGE]       -> "See Coach Handbook" (varies by assignment)
      [X]                -> highest grade number
      [Category]         -> highest grade category
      [GRADE LEVEL]      -> current grade description
    """
    profile = member.profile

    # Build address string
    address_parts = []
    if profile and profile.address:
        address_parts.append(profile.address)
    if profile and profile.city:
        address_parts.append(profile.city)
    if profile and profile.state:
        address_parts.append(profile.state)
    address_str = ", ".join(address_parts) if address_parts else "Not provided"

    phone_str = profile.phone if profile and profile.phone else "Not provided"
    full_name = f"{member.first_name} {member.last_name}"

    # Determine highest grade from category grades
    grade_fields = {
        "Learn to Swim": coach_profile.learn_to_swim_grade,
        "Special Populations": coach_profile.special_populations_grade,
        "Institutional": coach_profile.institutional_grade,
        "Competitive/Elite": coach_profile.competitive_elite_grade,
        "Certifications": coach_profile.certifications_grade,
        "Specialized Disciplines": coach_profile.specialized_disciplines_grade,
        "Adjacent Services": coach_profile.adjacent_services_grade,
    }
    grade_order = {"grade_1": 1, "grade_2": 2, "grade_3": 3}
    highest_grade = None
    highest_category = None
    highest_num = 0
    for category, grade_val in grade_fields.items():
        if grade_val and grade_order.get(grade_val, 0) > highest_num:
            highest_num = grade_order[grade_val]
            highest_grade = grade_val
            highest_category = category

    grade_labels = {
        "grade_1": "Grade 1 – Foundational",
        "grade_2": "Grade 2 – Technical",
        "grade_3": "Grade 3 – Advanced/Specialist",
    }
    has_grades = highest_grade is not None

    # Perform replacements
    rendered = template_content
    rendered = rendered.replace("[DATE]", date_type.today().strftime("%B %d, %Y"))
    rendered = rendered.replace("[COACH FULL NAME]", full_name)
    rendered = rendered.replace("[Coach Address]", address_str)
    rendered = rendered.replace("[Phone Number]", phone_str)
    rendered = rendered.replace("[Email Address]", member.email)

    # Grade level
    if has_grades:
        rendered = rendered.replace("[GRADE LEVEL]", grade_labels[highest_grade])
    else:
        rendered = rendered.replace(
            "Current Grade: **[GRADE LEVEL]**",
            "Current Grade: **To be determined upon assignment**",
        )
        # Fallback if pattern doesn't match exactly
        rendered = rendered.replace("[GRADE LEVEL]", "To be determined upon assignment")

    # Revenue share line: **[PERCENTAGE]%** (Grade [X], [Category])
    # The template has: **[PERCENTAGE]%** (Grade [X], [Category])
    if has_grades:
        grade_num_str = str(highest_num)
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            f"**See Coach Handbook** (Grade {grade_num_str}, {highest_category})",
        )
    else:
        rendered = rendered.replace(
            "**[PERCENTAGE]%** (Grade [X], [Category])",
            "**To be determined upon grade assignment** (see Coach Handbook for pay bands)",
        )

    # Fallback for any remaining individual placeholders
    rendered = rendered.replace("[PERCENTAGE]", "TBD")
    rendered = rendered.replace("[X]", str(highest_num) if has_grades else "TBD")
    rendered = rendered.replace("[Category]", highest_category or "TBD")

    return rendered


async def _get_current_agreement_version(session) -> AgreementVersion:
    """Get the current agreement version from the database.

    Raises HTTPException(404) if no current version exists.
    """
    result = await session.execute(
        select(AgreementVersion).where(AgreementVersion.is_current.is_(True))
    )
    current = result.scalar_one_or_none()
    if not current:
        raise HTTPException(
            status_code=404,
            detail="No current agreement version found. Contact an administrator.",
        )
    return current


# ============================================================================
# COACH AGREEMENT ENDPOINTS
# ============================================================================


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


# ============================================================================
# ADMIN AGREEMENT VERSION MANAGEMENT
# ============================================================================


@admin_router.get("/agreements", response_model=list[AgreementVersionListItem])
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


@admin_router.post("/agreements", response_model=AgreementVersionDetail)
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


@admin_router.get("/agreements/{version_id}", response_model=AgreementVersionDetail)
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


# ============================================================================
# HANDBOOK ENDPOINTS
# ============================================================================


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


@admin_router.get("/handbook/versions", response_model=list[HandbookVersionListItem])
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


@admin_router.post("/handbook", response_model=HandbookVersionDetail)
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
