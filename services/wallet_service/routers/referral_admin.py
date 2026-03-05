"""Admin referral management endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import ReferralStatus
from services.wallet_service.models.referral import ReferralCode, ReferralRecord
from services.wallet_service.schemas import (
    AdminReferralListResponse,
    AdminReferralProgramStats,
    LeaderboardEntry,
    ReferralHistoryItem,
    ReferralLeaderboardResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/admin/wallet/referrals", tags=["admin-referral"])


@router.get("/", response_model=AdminReferralListResponse)
async def list_referrals(
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all referral records with optional status filter."""
    query = select(ReferralRecord)
    count_query = select(func.count(ReferralRecord.id))

    if status_filter:
        query = query.where(ReferralRecord.status == ReferralStatus(status_filter))
        count_query = count_query.where(
            ReferralRecord.status == ReferralStatus(status_filter)
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(ReferralRecord.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    records = result.scalars().all()

    return AdminReferralListResponse(
        items=[ReferralHistoryItem.model_validate(r) for r in records],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/stats", response_model=AdminReferralProgramStats)
async def get_program_stats(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get program-wide referral statistics."""
    codes_result = await db.execute(select(func.count(ReferralCode.id)))
    total_codes = codes_result.scalar() or 0

    # Count referral records by status
    status_result = await db.execute(
        select(
            ReferralRecord.status,
            func.count(ReferralRecord.id).label("count"),
        ).group_by(ReferralRecord.status)
    )
    status_counts = {row[0].value: row[1] for row in status_result.all()}

    total_registrations = sum(status_counts.values())
    total_qualified = status_counts.get("qualified", 0)
    total_rewarded = status_counts.get("rewarded", 0)

    # Total bubbles distributed
    bubbles_result = await db.execute(
        select(
            func.coalesce(func.sum(ReferralRecord.referrer_reward_bubbles), 0)
            + func.coalesce(func.sum(ReferralRecord.referee_reward_bubbles), 0)
        )
    )
    total_bubbles = bubbles_result.scalar() or 0

    conversion_rate = (
        (total_qualified + total_rewarded) / total_registrations * 100
        if total_registrations > 0
        else 0.0
    )

    return AdminReferralProgramStats(
        total_codes_generated=total_codes,
        total_registrations=total_registrations,
        total_qualified=total_qualified,
        total_rewarded=total_rewarded,
        conversion_rate=round(conversion_rate, 1),
        total_bubbles_distributed=total_bubbles,
    )


@router.patch("/{referral_id}")
async def update_referral_status(
    referral_id: uuid.UUID,
    action: str = Query(..., description="Action: 'cancel' or 'qualify'"),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel or manually qualify a referral record."""
    result = await db.execute(
        select(ReferralRecord).where(ReferralRecord.id == referral_id)
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail="Referral record not found.")

    if action == "cancel":
        if record.status in (ReferralStatus.REWARDED, ReferralStatus.CANCELLED):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel a referral with status '{record.status.value}'.",
            )
        record.status = ReferralStatus.CANCELLED
        await db.commit()
        return {"success": True, "message": "Referral cancelled."}

    if action == "qualify":
        if record.status != ReferralStatus.REGISTERED:
            raise HTTPException(
                status_code=400,
                detail=f"Can only qualify referrals with status 'registered', got '{record.status.value}'.",
            )
        # Use the service to qualify + distribute rewards
        from services.wallet_service.services.referral_service import (
            check_and_qualify_referral,
        )

        updated = await check_and_qualify_referral(
            record.referee_auth_id, "admin_manual", db
        )
        return {
            "success": True,
            "message": "Referral qualified and rewards distributed.",
            "status": updated.status.value if updated else "unknown",
        }

    raise HTTPException(
        status_code=400,
        detail="Invalid action. Use 'cancel' or 'qualify'.",
    )


@router.get("/leaderboard", response_model=ReferralLeaderboardResponse)
async def get_referral_leaderboard(
    period: str = Query("all_time", description="all_time, this_month, this_year"),
    limit: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin referral leaderboard — top referrers by successful referrals."""
    query = select(ReferralCode).where(ReferralCode.successful_referrals > 0)

    if period == "this_month":
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # For this_month, filter referral codes that had activity this month
        # We use the code's last_used_at as a proxy
        query = query.where(ReferralCode.last_used_at >= month_start)
    elif period == "this_year":
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        year_start = now.replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        )
        query = query.where(ReferralCode.last_used_at >= year_start)

    query = query.order_by(ReferralCode.successful_referrals.desc()).limit(limit)
    result = await db.execute(query)
    codes = result.scalars().all()

    entries = []
    for rank, code in enumerate(codes, 1):
        uses = code.uses_count or 1
        conversion = code.successful_referrals / uses * 100 if uses > 0 else 0.0

        # Total bubbles earned as referrer
        bubbles_result = await db.execute(
            select(
                func.coalesce(func.sum(ReferralRecord.referrer_reward_bubbles), 0)
            ).where(ReferralRecord.referrer_auth_id == code.member_auth_id)
        )
        total_bubbles = bubbles_result.scalar_one()

        # Resolve member name (best-effort)
        member_name = None
        try:
            member = await get_member_by_auth_id(
                code.member_auth_id, calling_service="wallet"
            )
            if member:
                first = member.get("first_name", "")
                last = member.get("last_name", "")
                member_name = f"{first} {last}".strip() or None
        except Exception:
            pass

        entries.append(
            LeaderboardEntry(
                rank=rank,
                member_auth_id=code.member_auth_id,
                member_name=member_name,
                referral_code=code.code,
                successful_referrals=code.successful_referrals,
                total_bubbles_earned=total_bubbles,
                conversion_rate=round(conversion, 1),
            )
        )

    return ReferralLeaderboardResponse(entries=entries, period=period)
