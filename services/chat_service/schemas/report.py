"""Moderation-report request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.chat_service.models.enums import ReportReason, ReportStatus


class ReportCreateRequest(BaseModel):
    reason: ReportReason
    note: Optional[str] = Field(default=None, max_length=2000)


class ReportOut(BaseModel):
    id: uuid.UUID
    message_id: uuid.UUID
    reporter_id: uuid.UUID
    reason: ReportReason
    note: Optional[str] = None
    status: ReportStatus
    assigned_to: Optional[uuid.UUID] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
