"""Internal service-to-service endpoints for transport-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from services.transport_service.models import (
    PickupLocation,
    RideBooking,
    SessionRideConfig,
)

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


class BundleRideQuoteSelection(BaseModel):
    session_id: uuid.UUID
    ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    num_seats: int = Field(default=1, ge=1, le=20)


class BundleRideQuoteRequest(BaseModel):
    member_id: uuid.UUID
    selections: List[BundleRideQuoteSelection] = Field(max_length=10)

    @field_validator("selections")
    @classmethod
    def sessions_are_unique(
        cls, value: List[BundleRideQuoteSelection]
    ) -> List[BundleRideQuoteSelection]:
        session_ids = [selection.session_id for selection in value]
        if len(set(session_ids)) != len(session_ids):
            raise ValueError("Only one ride selection is allowed per session")
        return value


class BundleRideQuoteLine(BaseModel):
    session_id: uuid.UUID
    ride_config_id: uuid.UUID
    pickup_location_id: uuid.UUID
    num_seats: int
    unit_amount_kobo: int
    amount_kobo: int


class BundleRideQuoteResponse(BaseModel):
    total_kobo: int
    lines: List[BundleRideQuoteLine]


@router.post("/ride-quotes", response_model=BundleRideQuoteResponse)
async def quote_bundle_rides(
    payload: BundleRideQuoteRequest,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> BundleRideQuoteResponse:
    """Validate bundle ride selections and calculate their authoritative total."""
    if not payload.selections:
        return BundleRideQuoteResponse(total_kobo=0, lines=[])

    config_ids = [selection.ride_config_id for selection in payload.selections]
    pickup_ids = [selection.pickup_location_id for selection in payload.selections]
    configs = (
        (
            await db.execute(
                select(SessionRideConfig).where(SessionRideConfig.id.in_(config_ids))
            )
        )
        .scalars()
        .all()
    )
    pickups = (
        (
            await db.execute(
                select(PickupLocation).where(PickupLocation.id.in_(pickup_ids))
            )
        )
        .scalars()
        .all()
    )
    config_map = {config.id: config for config in configs}
    pickup_map = {pickup.id: pickup for pickup in pickups}

    selected_session_ids = [selection.session_id for selection in payload.selections]
    existing = (
        (
            await db.execute(
                select(RideBooking.session_id).where(
                    RideBooking.member_id == payload.member_id,
                    RideBooking.session_id.in_(selected_session_ids),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A ride is already booked for one or more selected sessions",
        )

    lines: list[BundleRideQuoteLine] = []
    for selection in payload.selections:
        config = config_map.get(selection.ride_config_id)
        if config is None or config.session_id != selection.session_id:
            raise HTTPException(
                status_code=400,
                detail="A selected ride configuration does not belong to its session",
            )
        pickup = pickup_map.get(selection.pickup_location_id)
        if (
            pickup is None
            or not pickup.is_active
            or pickup.area_id != config.ride_area_id
        ):
            raise HTTPException(
                status_code=400,
                detail="A selected pickup location is not valid for its ride",
            )
        unit_amount_kobo = int(config.cost or 0)
        lines.append(
            BundleRideQuoteLine(
                session_id=selection.session_id,
                ride_config_id=selection.ride_config_id,
                pickup_location_id=selection.pickup_location_id,
                num_seats=selection.num_seats,
                unit_amount_kobo=unit_amount_kobo,
                amount_kobo=unit_amount_kobo * selection.num_seats,
            )
        )

    return BundleRideQuoteResponse(
        total_kobo=sum(line.amount_kobo for line in lines),
        lines=lines,
    )


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
