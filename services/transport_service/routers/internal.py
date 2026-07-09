"""Internal service-to-service endpoints for transport-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from services.transport_service.models import SessionRideConfig

router = APIRouter(prefix="/internal/transport", tags=["internal"])


class MemberTransportSummary(BaseModel):
    rides_taken: int = 0
    rides_offered: int = 0


class InternalSessionRideConfigCreate(BaseModel):
    ride_area_id: uuid.UUID
    cost: float = 0.0
    capacity: int = 4
    departure_time: Optional[datetime] = None


class InternalSessionRideConfigAttachResponse(BaseModel):
    created: int


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


@router.post(
    "/sessions/{session_id}/ride-configs",
    response_model=InternalSessionRideConfigAttachResponse,
)
async def attach_ride_configs_internal(
    session_id: uuid.UUID,
    configs_in: List[InternalSessionRideConfigCreate],
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Attach ride areas to a session from another backend service.

    This mirrors the admin route's replace strategy, but authenticates via
    service-role so sessions_service can materialise template ride configs
    without an interactive admin token.
    """
    await db.execute(
        delete(SessionRideConfig).where(SessionRideConfig.session_id == session_id)
    )

    for cfg_data in configs_in:
        db.add(
            SessionRideConfig(
                session_id=session_id,
                ride_area_id=cfg_data.ride_area_id,
                cost=naira_to_kobo(cfg_data.cost),
                capacity=cfg_data.capacity,
                departure_time=cfg_data.departure_time,
            )
        )

    await db.commit()
    return InternalSessionRideConfigAttachResponse(created=len(configs_in))
