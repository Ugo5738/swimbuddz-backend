"""Service-owned Club finance operational summary for gateway aggregation."""

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.members_service.models.club import ClubEnrollment
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/club-finance-summary", dependencies=[Depends(require_service_role)])
async def club_finance_summary(db: AsyncSession = Depends(get_async_db)):
    """Return active dated Club enrollments owned by members_service."""
    now = utc_now()
    club_active = int(
        await db.scalar(
            select(func.count(ClubEnrollment.id)).where(
                ClubEnrollment.status == "active",
                ClubEnrollment.starts_at <= now,
                ClubEnrollment.ends_at > now,
            )
        )
        or 0
    )
    return {
        "currently_active_enrollments": club_active,
        "future_service_costs_minor": None,
        "warning": "Club session/pool/coach cost forecast requires approved rate and session commitments.",
    }
