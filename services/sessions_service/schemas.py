import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.sessions_service.models import SessionLocation


class SessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    location: SessionLocation
    pool_fee: float = 0.0
    capacity: int = 20
    start_time: datetime
    end_time: datetime


class SessionCreate(SessionBase):
    pass


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[SessionLocation] = None
    pool_fee: Optional[float] = None
    capacity: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class SessionResponse(SessionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    template_id: Optional[uuid.UUID] = None
    is_recurring_instance: bool = False

    model_config = ConfigDict(from_attributes=True)
