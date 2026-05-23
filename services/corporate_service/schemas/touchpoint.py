"""Pydantic schemas for CorporateTouchpoint."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.corporate_service.models.enums import (
    TouchpointDirection,
    TouchpointType,
)


class CorporateTouchpointBase(BaseModel):
    type: TouchpointType
    direction: TouchpointDirection = TouchpointDirection.OUTBOUND
    occurred_at: Optional[datetime] = None  # defaults to utc_now in the route
    summary: Optional[str] = Field(None, max_length=500)
    outcome: Optional[str] = None
    next_action: Optional[str] = None
    deal_id: Optional[uuid.UUID] = None


class CorporateTouchpointCreate(CorporateTouchpointBase):
    pass


class CorporateTouchpointResponse(CorporateTouchpointBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    logged_by_auth_id: Optional[str] = None
    created_at: datetime
    # occurred_at is required on responses (default applied on create)
    occurred_at: datetime
