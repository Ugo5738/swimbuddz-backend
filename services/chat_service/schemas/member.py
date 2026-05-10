"""Channel-member request/response schemas."""

import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.chat_service.models.enums import (
    ChannelMemberRole,
    MembershipDerivation,
)


class ChannelMemberOut(BaseModel):
    channel_id: uuid.UUID
    member_id: uuid.UUID
    role: ChannelMemberRole
    joined_at: datetime
    left_at: Optional[datetime] = None
    muted_until: Optional[datetime] = None
    last_read_message_id: Optional[uuid.UUID] = None
    derived_from: MembershipDerivation
    derivation_ref: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class ReconcileAction(str, enum.Enum):
    """Add or remove (soft) a member from a channel — used by upstream
    services when their derivation source changes (enrollment, RSVP, …)."""

    ADD = "add"
    REMOVE = "remove"


class ReconcileMembershipRequest(BaseModel):
    """Internal: align chat membership with an upstream parent state change.

    Either `channel_id` OR (`parent_entity_type` + `parent_entity_id`) must
    be supplied. The (type, id) form lets callers reconcile without first
    looking up the channel.
    """

    channel_id: Optional[uuid.UUID] = None
    parent_entity_type: Optional[str] = None
    parent_entity_id: Optional[uuid.UUID] = None
    member_id: uuid.UUID
    action: ReconcileAction
    role: ChannelMemberRole = ChannelMemberRole.MEMBER
    derived_from: MembershipDerivation = MembershipDerivation.MANUAL
    derivation_ref: Optional[uuid.UUID] = None


class ReconcileMembershipResponse(BaseModel):
    channel_id: uuid.UUID
    member_id: uuid.UUID
    action_taken: ReconcileAction
    role: ChannelMemberRole
