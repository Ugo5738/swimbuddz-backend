import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.communications_service.models import AnnouncementCategory


class AnnouncementBase(BaseModel):
    title: str
    summary: Optional[str] = None
    body: str
    category: AnnouncementCategory = AnnouncementCategory.GENERAL
    is_pinned: bool = False
    published_at: datetime


class AnnouncementCreate(AnnouncementBase):
    pass


class AnnouncementResponse(AnnouncementBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
