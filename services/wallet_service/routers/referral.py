"""Member-facing referral endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import ReferralStatus
from services.wallet_service.models.referral import ReferralCode, ReferralRecord
from services.wallet_service.schemas import (
    AmbassadorStatusResponse,
    LeaderboardEntry,
    ReferralApplyRequest,
    ReferralApplyResponse,
    ReferralCodeResponse,
    ReferralCodeValidateResponse,
    ReferralHistoryItem,
    ReferralLeaderboardResponse,
    ReferralStatsResponse,
)
from services.wallet_service.services.referral_service import (
    apply_referral_code,
    get_or_create_referral_code,
    get_referral_history,
    get_referral_stats,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/wallet/referral", tags=["referral"])


def _share_base_url() -> str:
    """Build the referral share base URL from FRONTEND_URL setting."""
    from libs.common.config import get_settings

    return f"{get_settings().FRONTEND_URL.rstrip('/')}/join"


@router.get("/code", response_model=ReferralCodeResponse)
async def get_my_referral_code(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get or create the current member's referral code."""
    code_obj = await get_or_create_referral_code(current_user.user_id, db)

    share_link = f"{_share_base_url()}?ref={code_obj.code}"
    share_text = (
        f"Join SwimBuddz and get bonus Bubbles! "
        f"Use my referral code {code_obj.code} when you sign up: {share_link}"
    )

    return ReferralCodeResponse(
        code=code_obj.code,
        share_link=share_link,
        share_text=share_text,
        is_active=code_obj.is_active,
        uses_count=code_obj.uses_count,
        successful_referrals=code_obj.successful_referrals,
        max_uses=code_obj.max_uses,
        expires_at=code_obj.expires_at,
        created_at=code_obj.created_at,
    )


@router.get("/validate", response_model=ReferralCodeValidateResponse)
async def validate_referral_code(
    code: str = Query(..., min_length=1, description="Referral code to validate"),
    db: AsyncSession = Depends(get_async_db),
):
    """Validate a referral code (public, no auth required).

    Used by the /join?ref=CODE landing page to check if a code is valid
    before the user registers.
    """
    from libs.common.datetime_utils import utc_now

    normalized = code.upper().strip()
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.code == normalized)
    )
    code_obj = result.scalar_one_or_none()

    if not code_obj:
        return ReferralCodeValidateResponse(
            valid=False, code=normalized, message="Referral code not found."
        )

    now = utc_now()

    if not code_obj.is_active:
        return ReferralCodeValidateResponse(
            valid=False,
            code=normalized,
            message="This referral code is no longer active.",
        )

    if code_obj.expires_at and code_obj.expires_at < now:
        return ReferralCodeValidateResponse(
            valid=False, code=normalized, message="This referral code has expired."
        )

    if code_obj.max_uses and code_obj.uses_count >= code_obj.max_uses:
        return ReferralCodeValidateResponse(
            valid=False,
            code=normalized,
            message="This referral code has reached its maximum uses.",
        )

    return ReferralCodeValidateResponse(
        valid=True, code=normalized, message="Referral code is valid!"
    )


@router.get("/stats", response_model=ReferralStatsResponse)
async def get_my_referral_stats(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get referral statistics for the current member."""
    stats = await get_referral_stats(current_user.user_id, db)
    return ReferralStatsResponse(**stats)


@router.get("/history", response_model=list[ReferralHistoryItem])
async def get_my_referral_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get paginated referral history for the current member."""
    records = await get_referral_history(
        current_user.user_id, db, skip=skip, limit=limit
    )

    # Resolve referee names (best-effort — fall back to "A friend" on failure)
    items: list[ReferralHistoryItem] = []
    for r in records:
        item = ReferralHistoryItem.model_validate(r)
        try:
            member = await get_member_by_auth_id(
                r.referee_auth_id, calling_service="wallet"
            )
            if member:
                first = member.get("first_name", "")
                last = member.get("last_name", "")
                item.referee_name = f"{first} {last}".strip() or None
        except Exception:
            pass
        items.append(item)

    return items


@router.post("/apply", response_model=ReferralApplyResponse)
async def apply_referral(
    body: ReferralApplyRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply a referral code for the current member."""
    try:
        await apply_referral_code(current_user.user_id, body.code, db)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    return ReferralApplyResponse(
        success=True,
        message="Referral code applied successfully! You'll both earn Bubbles once you qualify.",
    )


AMBASSADOR_THRESHOLD = 10


@router.get("/ambassador", response_model=AmbassadorStatusResponse)
async def get_ambassador_status(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the current member's ambassador badge status."""
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.member_auth_id == current_user.user_id)
    )
    code_obj = result.scalar_one_or_none()

    if not code_obj:
        return AmbassadorStatusResponse(
            is_ambassador=False,
            successful_referrals=0,
            referrals_to_ambassador=AMBASSADOR_THRESHOLD,
            ambassador_since=None,
            total_referral_bubbles_earned=0,
        )

    successful = code_obj.successful_referrals
    is_ambassador = successful >= AMBASSADOR_THRESHOLD

    # Total bubbles earned from referrals (as referrer)
    bubbles_result = await db.execute(
        select(
            func.coalesce(func.sum(ReferralRecord.referrer_reward_bubbles), 0)
        ).where(ReferralRecord.referrer_auth_id == current_user.user_id)
    )
    total_bubbles = bubbles_result.scalar_one()

    # Approximate ambassador_since: the rewarded_at of the Nth referral
    ambassador_since = None
    if is_ambassador:
        nth_result = await db.execute(
            select(ReferralRecord.rewarded_at)
            .where(
                ReferralRecord.referrer_auth_id == current_user.user_id,
                ReferralRecord.status == ReferralStatus.REWARDED,
            )
            .order_by(ReferralRecord.rewarded_at.asc())
            .offset(AMBASSADOR_THRESHOLD - 1)
            .limit(1)
        )
        row = nth_result.scalar_one_or_none()
        if row:
            ambassador_since = row

    return AmbassadorStatusResponse(
        is_ambassador=is_ambassador,
        successful_referrals=successful,
        referrals_to_ambassador=max(0, AMBASSADOR_THRESHOLD - successful),
        ambassador_since=ambassador_since,
        total_referral_bubbles_earned=total_bubbles,
    )


@router.get("/leaderboard", response_model=ReferralLeaderboardResponse)
async def get_public_leaderboard(
    db: AsyncSession = Depends(get_async_db),
    _user: AuthUser = Depends(get_current_user),
):
    """Public referral leaderboard (top 10, codes partially anonymized)."""
    result = await db.execute(
        select(ReferralCode)
        .where(ReferralCode.successful_referrals > 0)
        .order_by(ReferralCode.successful_referrals.desc())
        .limit(10)
    )
    codes = result.scalars().all()

    entries = []
    for rank, code in enumerate(codes, 1):
        uses = code.uses_count or 1
        conversion = code.successful_referrals / uses * 100 if uses > 0 else 0.0

        # Compute total bubbles for this referrer
        bubbles_result = await db.execute(
            select(
                func.coalesce(func.sum(ReferralRecord.referrer_reward_bubbles), 0)
            ).where(ReferralRecord.referrer_auth_id == code.member_auth_id)
        )
        total_bubbles = bubbles_result.scalar_one()

        # Anonymize: show first 2 chars of code + mask
        anonymized = code.code[:5] + "***"

        entries.append(
            LeaderboardEntry(
                rank=rank,
                member_auth_id="",  # Hidden on public leaderboard
                referral_code=anonymized,
                successful_referrals=code.successful_referrals,
                total_bubbles_earned=total_bubbles,
                conversion_rate=round(conversion, 1),
            )
        )

    return ReferralLeaderboardResponse(entries=entries, period="all_time")
