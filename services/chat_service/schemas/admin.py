"""Admin / moderator request and response schemas."""

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.chat_service.models.enums import (
    ChannelMemberRole,
    ChatAuditAction,
    ReportReason,
    ReportStatus,
)


class AdminMessageDeleteRequest(BaseModel):
    """Body for admin hard-delete. Note is required so the audit row records
    *why* — hard-delete is a high-trust action, especially in minor channels."""

    note: str = Field(..., min_length=1, max_length=500)


class ReportListItem(BaseModel):
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
    # Lightweight attached context — saves an extra round-trip from the moderator UI.
    channel_id: Optional[uuid.UUID] = None
    sender_id: Optional[uuid.UUID] = None
    body_preview: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ReportResolveRequest(BaseModel):
    """Resolve, dismiss, or assign a report.

    `status` transitions: open → under_review → (resolved | dismissed).
    `assigned_to` is independent — assignment can move while status stays open.
    """

    status: Optional[ReportStatus] = None
    assigned_to: Optional[uuid.UUID] = None
    resolution_note: Optional[str] = Field(default=None, max_length=2000)


class AdminMemberRoleUpdateRequest(BaseModel):
    role: ChannelMemberRole


class AuditLogItem(BaseModel):
    id: uuid.UUID
    actor_id: Optional[uuid.UUID] = None
    action: ChatAuditAction
    channel_id: Optional[uuid.UUID] = None
    message_id: Optional[uuid.UUID] = None
    subject_member_id: Optional[uuid.UUID] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogPage(BaseModel):
    """Cursor-paginated audit log slice. Cursor is `next_before_id` — the
    oldest entry's id in the current page."""

    items: list[AuditLogItem]
    next_before_id: Optional[uuid.UUID] = None
    has_more: bool = False
