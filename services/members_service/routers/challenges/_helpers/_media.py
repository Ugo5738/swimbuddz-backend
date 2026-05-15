"""Media + roster loaders.

Join-and-hydrate queries for ChallengeExampleMedia (admin example
photos), ChallengeSubmissionMedia (member-uploaded proof), and the
ChallengeSubmissionMember roster (with full names resolved).
"""

import uuid
from typing import List, Literal, Optional

from fastapi import HTTPException
from libs.auth.dependencies import is_admin_or_service
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.service_client import (
    dispatch_notification,
    grant_challenge_reward_bubbles,
    grant_challenge_volunteer_hours,
)
from services.members_service.models import (
    ChallengeBadgeAward,
    ChallengeExampleMedia,
    ChallengeSubmissionMedia,
    ChallengeSubmissionMember,
    ClubChallenge,
    Member,
    MemberChallengeCompletion,
    Pod,
    PodAssignment,
)
from services.members_service.schemas import (
    ChallengeExampleMediaResponse,
    ChallengePublicResponse,
    ChallengeSubmissionMediaResponse,
    ChallengeSubmissionMemberResponse,
    ChallengeSubmissionResponse,
    ChallengeWinnerPublicInfo,
    ClubChallengeResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)

from ._members import _load_member_names

async def _load_challenge_example_media(
    challenge_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeExampleMediaResponse]:
    """Load example media join rows for a challenge with hydrated URLs.

    Hydrates file_url via a single bulk HTTP call to media_service
    (services/libs/common/media_utils.py:resolve_media_urls). thumbnail_url
    falls back to file_url for now — the media service returns image URLs
    that work for both purposes; videos can use the same URL on a poster.
    """
    rows = await db.execute(
        select(ChallengeExampleMedia)
        .where(ChallengeExampleMedia.challenge_id == challenge_id)
        .order_by(ChallengeExampleMedia.order_idx, ChallengeExampleMedia.created_at)
    )
    items = list(rows.scalars().all())
    url_map = await resolve_media_urls([m.media_id for m in items])
    out: List[ChallengeExampleMediaResponse] = []
    for row in items:
        item = ChallengeExampleMediaResponse.model_validate(row)
        url = url_map.get(row.media_id) or url_map.get(str(row.media_id))
        if url:
            item.file_url = url
            # No separate thumbnail in the media-service response shape;
            # callers can render <video poster=...> with the same URL or
            # let the browser auto-generate.
            item.thumbnail_url = url
        out.append(item)
    return out

async def _load_submission_proof_media(
    submission_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeSubmissionMediaResponse]:
    rows = await db.execute(
        select(ChallengeSubmissionMedia)
        .where(ChallengeSubmissionMedia.submission_id == submission_id)
        .order_by(
            ChallengeSubmissionMedia.order_idx, ChallengeSubmissionMedia.created_at
        )
    )
    items = list(rows.scalars().all())
    url_map = await resolve_media_urls([m.media_id for m in items])
    out: List[ChallengeSubmissionMediaResponse] = []
    for row in items:
        item = ChallengeSubmissionMediaResponse.model_validate(row)
        url = url_map.get(row.media_id) or url_map.get(str(row.media_id))
        if url:
            item.file_url = url
            item.thumbnail_url = url
        out.append(item)
    return out

async def _load_submission_members(
    submission_id: uuid.UUID, db: AsyncSession
) -> List[ChallengeSubmissionMemberResponse]:
    """Return per-member roster rows with full names hydrated.

    Names come from a single bulk lookup against the local Member table —
    admin-facing UIs render member names rather than raw UUIDs.
    """
    rows = await db.execute(
        select(ChallengeSubmissionMember)
        .where(ChallengeSubmissionMember.submission_id == submission_id)
        .order_by(ChallengeSubmissionMember.created_at)
    )
    items = list(rows.scalars().all())
    if not items:
        return []

    name_map = await _load_member_names([m.member_id for m in items], db)
    out: List[ChallengeSubmissionMemberResponse] = []
    for row in items:
        item = ChallengeSubmissionMemberResponse.model_validate(row)
        item.member_name = name_map.get(row.member_id)
        out.append(item)
    return out
