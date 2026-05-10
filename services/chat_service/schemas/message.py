"""Message request/response schemas."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.chat_service.schemas.common import MAX_MESSAGE_BODY_LENGTH


class ReactionSummary(BaseModel):
    """Aggregated reactions on a message: how many of each emoji, and whether
    the caller has reacted."""

    emoji: str
    count: int
    reacted_by_me: bool = False


class MessageOut(BaseModel):
    id: uuid.UUID
    channel_id: uuid.UUID
    sender_id: uuid.UUID
    body: str  # for soft-deleted rows this is the placeholder string
    attachments: list = Field(default_factory=list)
    reply_to_id: Optional[uuid.UUID] = None
    created_at: datetime
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    reactions: list[ReactionSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MessageSendRequest(BaseModel):
    """Send a message. `client_message_id` is the canonical idempotency key
    — clients generate it before sending and reuse it on retry. The server
    uses it as the row's primary key, so duplicate POSTs converge on one row.
    """

    body: str = Field(..., min_length=1, max_length=MAX_MESSAGE_BODY_LENGTH)
    attachments: list = Field(default_factory=list)
    reply_to_id: Optional[uuid.UUID] = None
    client_message_id: uuid.UUID


class MessageEditRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=MAX_MESSAGE_BODY_LENGTH)


class MessageListPage(BaseModel):
    """Cursor-paginated list of messages in a channel.

    Cursor is the oldest message's id in the current page — pass it as the
    next request's `before_id` to fetch older messages. `has_more` is true
    when more history exists.
    """

    items: list[MessageOut]
    next_before_id: Optional[uuid.UUID] = None
    has_more: bool = False
