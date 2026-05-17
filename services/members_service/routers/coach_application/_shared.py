"""Shared helpers for coach application + admin review routers."""

import re
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url
from libs.common.service_client import internal_post
from libs.db.config import AsyncSessionLocal
from services.members_service.models import CoachProfile, Member
from services.members_service.schemas import CoachApplicationResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)
settings = get_settings()


def _strip_internal_handbook_sections(content: str) -> str:
    """
    Coaches should not see internal-only appendices (e.g. Appendix B: system integration spec).
    Filter at the API boundary (defense in depth, even if the frontend also hides it).
    """
    m = re.search(r"^##\s+Appendix\s+B\b.*$", content, flags=re.MULTILINE)
    if not m:
        return content
    return content[: m.start()].rstrip() + "\n"


async def get_member_by_auth_id(auth_id: str) -> Optional[Member]:
    """Get member by Supabase auth ID."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member)
            .options(selectinload(Member.coach_profile))
            .where(Member.auth_id == auth_id)
        )
        return result.scalar_one_or_none()


async def get_coach_profile_by_member_id(member_id: str) -> Optional[CoachProfile]:
    """Get coach profile by member ID."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CoachProfile).where(CoachProfile.member_id == member_id)
        )
        return result.scalar_one_or_none()


async def _ensure_wallet_exists(member_id: str, member_auth_id: str) -> None:
    """Best-effort wallet auto-provisioning for coach accounts."""
    try:
        resp = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/create",
            calling_service="members",
            json={
                "member_id": member_id,
                "member_auth_id": member_auth_id,
            },
            timeout=15.0,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Wallet auto-create failed for coach member_auth_id=%s (http %d): %s",
                member_auth_id,
                resp.status_code,
                resp.text,
            )
    except Exception as exc:
        logger.warning(
            "Wallet auto-create request failed for coach member_auth_id=%s: %s",
            member_auth_id,
            exc,
        )


async def _build_coach_response(
    member: Member, coach: CoachProfile
) -> CoachApplicationResponse:
    """Build CoachApplicationResponse from Member and CoachProfile models."""
    coach_photo_url = (
        await resolve_media_url(coach.coach_profile_photo_media_id)
        if coach.coach_profile_photo_media_id
        else None
    )
    return CoachApplicationResponse(
        id=str(coach.id),
        member_id=str(member.id),
        email=member.email,
        first_name=member.first_name,
        last_name=member.last_name,
        display_name=coach.display_name,
        coach_profile_photo_media_id=coach.coach_profile_photo_media_id,
        coach_profile_photo_url=coach_photo_url,
        status=coach.status,
        short_bio=coach.short_bio,
        coaching_years=coach.coaching_years or 0,
        coaching_specialties=coach.coaching_specialties or [],
        certifications=coach.certifications or [],
        other_certifications_note=coach.other_certifications_note,
        levels_taught=coach.levels_taught,
        age_groups_taught=coach.age_groups_taught,
        languages_spoken=coach.languages_spoken,
        coaching_portfolio_link=coach.coaching_portfolio_link,
        has_cpr_training=coach.has_cpr_training,
        cpr_expiry_date=coach.cpr_expiry_date,
        coaching_document_link=coach.coaching_document_link,
        coaching_document_file_name=coach.coaching_document_file_name,
        application_submitted_at=coach.application_submitted_at,
        application_reviewed_at=coach.application_reviewed_at,
        rejection_reason=coach.rejection_reason,
        show_in_directory=coach.show_in_directory,
        created_at=coach.created_at,
        updated_at=coach.updated_at,
    )
