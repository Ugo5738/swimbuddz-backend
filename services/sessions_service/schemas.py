import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict
from services.sessions_service.models import SessionLocation, SessionType


class SessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    location: SessionLocation
    type: SessionType = SessionType.CLUB
    pool_fee: float = 0.0
    ride_share_fee: float = 0.0
    capacity: int = 20
    start_time: datetime
    end_time: datetime
    cohort_id: Optional[uuid.UUID] = None


class SessionCreate(SessionBase):
    pass


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[SessionLocation] = None
    type: Optional[SessionType] = None
    pool_fee: Optional[float] = None
    ride_share_fee: Optional[float] = None
    capacity: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    cohort_id: Optional[uuid.UUID] = None
    # TODO: Support updating ride share areas if needed


class SessionResponse(SessionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    template_id: Optional[uuid.UUID] = None
    cohort_id: Optional[uuid.UUID] = None
    is_recurring_instance: bool = False

    model_config = ConfigDict(from_attributes=True)
