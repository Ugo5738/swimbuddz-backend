"""Internal service-to-service endpoints for transport-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db

router = APIRouter(prefix="/internal/transport", tags=["internal"])


class MemberTransportSummary(BaseModel):
    rides_taken: int = 0
    rides_offered: int = 0


@router.get(
    "/member-summary/{member_auth_id}",
    response_model=MemberTransportSummary,
)
async def get_member_transport_summary(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate transport/ride-share stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    Looks up member_id from auth_id via raw SQL on the shared members table.
    """
    from sqlalchemy import text

    from services.transport_service.models.core import RideBooking

    # Look up member_id from auth_id
    member_result = await db.execute(
        text("SELECT id FROM members WHERE auth_id = :auth_id"),
        {"auth_id": member_auth_id},
    )
    row = member_result.first()
    if row is None:
        return MemberTransportSummary()

    member_uuid = row[0]

    result = await db.execute(
        select(func.count(RideBooking.id)).where(
            RideBooking.member_id == member_uuid,
            RideBooking.created_at >= date_from,
            RideBooking.created_at <= date_to,
        )
    )
    rides_taken = result.scalar() or 0

    return MemberTransportSummary(
        rides_taken=rides_taken,
        rides_offered=0,  # Placeholder — extend when driver tracking is implemented
    )
