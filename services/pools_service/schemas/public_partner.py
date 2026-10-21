"""Public partner-pool fields. Not the internal screening or commercial DTO."""

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.pools_service.models.enums import (
    IndoorOutdoor,
    PartnershipStatus,
    PoolType,
)


class PublicPartnerPool(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    location_area: Optional[str] = None
    address: Optional[str] = None
    operating_area_id: Optional[uuid.UUID] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    pool_type: Optional[PoolType] = None
    indoor_outdoor: Optional[IndoorOutdoor] = None
    pool_length_m: Optional[float] = None
    depth_min_m: Optional[float] = None
    depth_max_m: Optional[float] = None
    number_of_lanes: Optional[int] = None
    has_changing_rooms: Optional[bool] = None
    has_showers: Optional[bool] = None
    has_lockers: Optional[bool] = None
    has_parking: Optional[bool] = None
    has_lifeguard: Optional[bool] = None
    partnership_status: PartnershipStatus
    is_active: bool


class PublicPartnerPoolList(BaseModel):
    items: list[PublicPartnerPool]
    total: int
    page: int
    page_size: int
