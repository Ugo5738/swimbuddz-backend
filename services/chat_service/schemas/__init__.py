"""Chat Service schemas package."""

from services.chat_service.schemas.attachment import (
    AttachmentDescriptor,
    AttachmentModeration,
    AttachmentUploadResponse,
)
from services.chat_service.schemas.admin import (
    AdminMemberRoleUpdateRequest,
    AdminMessageDeleteRequest,
    AuditLogItem,
    AuditLogPage,
    ReportListItem,
    ReportResolveRequest,
)
from services.chat_service.schemas.channel import (
    ChannelDetail,
    ChannelMarkReadRequest,
    ChannelMuteRequest,
    ChannelSummary,
    EnsureChannelRequest,
    EnsureChannelResponse,
    LastMessagePreview,
)
from services.chat_service.schemas.common import (
    ALLOWED_REACTION_EMOJI,
    DEFAULT_MESSAGE_PAGE_SIZE,
    MAX_MESSAGE_BODY_LENGTH,
    MAX_MESSAGE_PAGE_SIZE,
)
from services.chat_service.schemas.member import (
    ChannelMemberOut,
    ReconcileAction,
    ReconcileMembershipRequest,
    ReconcileMembershipResponse,
)
from services.chat_service.schemas.message import (
    MessageEditRequest,
    MessageListPage,
    MessageOut,
    MessageSendRequest,
    ReactionSummary,
)
from services.chat_service.schemas.reaction import ReactionAddRequest, ReactionOut
from services.chat_service.schemas.report import ReportCreateRequest, ReportOut

__all__ = [
    # Common
    "ALLOWED_REACTION_EMOJI",
    "DEFAULT_MESSAGE_PAGE_SIZE",
    "MAX_MESSAGE_BODY_LENGTH",
    "MAX_MESSAGE_PAGE_SIZE",
    # Admin
    "AdminMemberRoleUpdateRequest",
    "AdminMessageDeleteRequest",
    "AuditLogItem",
    "AuditLogPage",
    "ReportListItem",
    "ReportResolveRequest",
    # Attachments
    "AttachmentDescriptor",
    "AttachmentModeration",
    "AttachmentUploadResponse",
    # Channel
    "ChannelDetail",
    "ChannelMarkReadRequest",
    "ChannelMuteRequest",
    "ChannelSummary",
    "EnsureChannelRequest",
    "EnsureChannelResponse",
    "LastMessagePreview",
    # Member
    "ChannelMemberOut",
    "ReconcileAction",
    "ReconcileMembershipRequest",
    "ReconcileMembershipResponse",
    # Message
    "MessageEditRequest",
    "MessageListPage",
    "MessageOut",
    "MessageSendRequest",
    "ReactionSummary",
    # Reaction
    "ReactionAddRequest",
    "ReactionOut",
    # Report
    "ReportCreateRequest",
    "ReportOut",
]
