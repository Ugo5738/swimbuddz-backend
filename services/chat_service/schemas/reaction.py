"""Reaction request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ReactionAddRequest(BaseModel):
    # Validation against the allowed set happens in the service layer (so the
    # error message can include the full allowed set without duplicating it
    # in every Field definition).
    emoji: str = Field(..., min_length=1, max_length=32)


class ReactionOut(BaseModel):
    message_id: uuid.UUID
    member_id: uuid.UUID
    emoji: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
