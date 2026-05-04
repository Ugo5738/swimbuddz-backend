"""Chat Service models package.

Re-exports all models and enums so that:
  - ``from services.chat_service.models import ChatChannel`` works unchanged
  - Alembic env.py imports continue to work without modification
  - SQLAlchemy's mapper registry sees every model class on import

IMPORTANT: every model class AND enum must be listed here. When adding a new
model, add both its import and its ``__all__`` entry. If you add a new model
table, also update ``services/chat_service/alembic/env.py::SERVICE_TABLES``
or Alembic won't detect it.
"""

from services.chat_service.models.audit import ChatAuditLog  # noqa: F401
from services.chat_service.models.channel import (  # noqa: F401
    ChatChannel,
    ChatChannelMember,
)
from services.chat_service.models.enums import (  # noqa: F401
    ChannelMemberRole,
    ChannelType,
    ChatAuditAction,
    MembershipDerivation,
    ParentEntityType,
    ReportReason,
    ReportStatus,
    RetentionPolicy,
    SafeguardingReviewState,
)
from services.chat_service.models.message import (  # noqa: F401
    ChatMessage,
    ChatMessageReaction,
)
from services.chat_service.models.report import ChatMessageReport  # noqa: F401

__all__ = [
    # Enums
    "ChannelType",
    "ParentEntityType",
    "ChannelMemberRole",
    "MembershipDerivation",
    "RetentionPolicy",
    "SafeguardingReviewState",
    "ReportReason",
    "ReportStatus",
    "ChatAuditAction",
    # Models
    "ChatChannel",
    "ChatChannelMember",
    "ChatMessage",
    "ChatMessageReaction",
    "ChatMessageReport",
    "ChatAuditLog",
]
