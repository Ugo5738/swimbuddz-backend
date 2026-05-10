"""Channel request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.chat_service.models.enums import (
    ChannelMemberRole,
    ChannelType,
    ParentEntityType,
    RetentionPolicy,
)


class LastMessagePreview(BaseModel):
    """Last visible message in a channel — used to render channel-list rows
    without having to fetch full history."""

    id: uuid.UUID
    sender_id: uuid.UUID
    body_preview: str  # truncated server-side to keep payloads small
    created_at: datetime


class ChannelSummary(BaseModel):
    """Minimal channel shape for the channel-list view."""

    id: uuid.UUID
    type: ChannelType
    parent_entity_type: ParentEntityType
    parent_entity_id: Optional[uuid.UUID] = None
    name: str
    archived_at: Optional[datetime] = None
    created_at: datetime

    # Per-caller fields — derived from the caller's ChatChannelMember row
    my_role: ChannelMemberRole
    my_muted_until: Optional[datetime] = None
    my_last_read_message_id: Optional[uuid.UUID] = None
    unread_count: int = 0
    last_message: Optional[LastMessagePreview] = None

    model_config = ConfigDict(from_attributes=True)


class ChannelDetail(ChannelSummary):
    """Channel detail view — adds settings & metadata not needed on the list."""

    description: Optional[str] = None
    retention_policy: RetentionPolicy
    created_by: Optional[uuid.UUID] = None
    safeguarding_flags: dict = Field(default_factory=dict)
    member_count: int = 0


class ChannelMarkReadRequest(BaseModel):
    """Move my last_read pointer up to (and including) `message_id`."""

    message_id: uuid.UUID


class ChannelMuteRequest(BaseModel):
    """Mute notifications for this channel until `muted_until`. Pass `null` to
    clear an existing mute."""

    muted_until: Optional[datetime] = None


class EnsureChannelRequest(BaseModel):
    """Internal: idempotent create-or-fetch for a channel tied to a parent
    entity (cohort, pod, event, trip, location, role).

    Lookup key is (parent_entity_type, parent_entity_id) — the same parent
    can only have one channel of each `type` (group / broadcast)."""

    type: ChannelType
    parent_entity_type: ParentEntityType
    parent_entity_id: Optional[uuid.UUID] = None
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    retention_policy: RetentionPolicy
    created_by: Optional[uuid.UUID] = None
    safeguarding_flags: dict = Field(default_factory=dict)


class EnsureChannelResponse(BaseModel):
    channel_id: uuid.UUID
    created: bool  # true on first call, false on subsequent (idempotent)
