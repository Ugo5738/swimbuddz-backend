"""Append-only chat audit log helper.

Every significant chat event flows through here so the audit trail stays in
one place. Callers pass the open AsyncSession — this function flushes but
never commits, so the audit row participates in the caller's transaction
(if the message-send rolls back, the audit row rolls back with it).

Writes use the canonical B4 audit shape (see ``libs.common.audit``):
``domain='chat'``, namespaced ``action`` (e.g. ``"chat.message_sent"``),
and ``entity_type``/``entity_id`` derived per-action from the chat scope
refs. Callers may pass ``old_value``/``new_value`` separately for proper
diff capture; legacy callers passing only ``payload`` get it stored on
``new_value`` (preserves data without losing the old contract)."""

import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.audit import DOMAIN_CHAT, make_action
from services.chat_service.models import ChatAuditAction, ChatAuditLog

# Per-action mapping to (entity_type, [scope refs to coalesce for entity_id]).
# The first non-null scope ref wins for entity_id. The canonical entity_id
# is NOT NULL, so every action must yield at least one populated ref —
# enforced at the bottom of log_action() with a clear error.
_ENTITY_MAP: dict[ChatAuditAction, tuple[str, tuple[str, ...]]] = {
    ChatAuditAction.MESSAGE_SENT: ("message", ("message_id",)),
    ChatAuditAction.MESSAGE_EDITED: ("message", ("message_id",)),
    ChatAuditAction.MESSAGE_DELETED: ("message", ("message_id",)),
    ChatAuditAction.CHANNEL_JOINED: ("channel", ("channel_id",)),
    ChatAuditAction.CHANNEL_LEFT: ("channel", ("channel_id",)),
    ChatAuditAction.CHANNEL_ARCHIVED: ("channel", ("channel_id",)),
    ChatAuditAction.MEMBER_ADDED: (
        "channel_membership",
        ("subject_member_id", "channel_id"),
    ),
    ChatAuditAction.MEMBER_REMOVED: (
        "channel_membership",
        ("subject_member_id", "channel_id"),
    ),
    ChatAuditAction.ROLE_CHANGED: (
        "channel_membership",
        ("subject_member_id", "channel_id"),
    ),
    ChatAuditAction.REPORT_FILED: (
        "report",
        ("message_id", "subject_member_id", "channel_id"),
    ),
    ChatAuditAction.REPORT_RESOLVED: (
        "report",
        ("message_id", "subject_member_id", "channel_id"),
    ),
    ChatAuditAction.SAFEGUARDING_ACTION: (
        "safeguarding",
        ("message_id", "channel_id", "subject_member_id"),
    ),
}


async def log_action(
    db: AsyncSession,
    *,
    action: ChatAuditAction,
    actor_id: Optional[uuid.UUID] = None,
    channel_id: Optional[uuid.UUID] = None,
    message_id: Optional[uuid.UUID] = None,
    subject_member_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> ChatAuditLog:
    """Record a chat audit event using the canonical (B4) shape.

    ``message_id`` is consumed only for ``entity_id`` derivation — the
    legacy column was dropped (B4 chat migration). For message-typed
    actions the message UUID is preserved on ``entity_id``.

    Backwards-compat: callers that only pass ``payload`` get it stored on
    ``new_value`` (with ``old_value`` left null). New code should pass
    ``old_value``/``new_value`` separately for proper diff capture.
    """
    entity_type, scope_keys = _ENTITY_MAP[action]
    scope_refs: dict[str, Optional[uuid.UUID]] = {
        "channel_id": channel_id,
        "message_id": message_id,
        "subject_member_id": subject_member_id,
    }
    entity_id: Optional[uuid.UUID] = next(
        (scope_refs[k] for k in scope_keys if scope_refs[k] is not None),
        None,
    )
    if entity_id is None:
        # Canonical contract: entity_id is NOT NULL. Every call must
        # supply at least one of the action's scope refs. Catch this
        # at write time with a clear error so the caller can fix it
        # instead of getting a generic DB IntegrityError.
        raise ValueError(
            f"chat audit log_action({action.value}): missing entity_id — "
            f"need one of {scope_keys} but all were None"
        )

    # Legacy callers pass `payload`; preserve their data on new_value.
    # New callers pass old_value/new_value explicitly.
    if payload is not None and new_value is None and old_value is None:
        new_value = payload

    entry = ChatAuditLog(
        domain=DOMAIN_CHAT,
        entity_type=entity_type,
        entity_id=entity_id,
        action=make_action(DOMAIN_CHAT, action.value),
        actor_id=actor_id,
        # actor_label is the human-readable actor when no UUID exists;
        # chat actors are always UUID-typed members or null (system),
        # so actor_label stays None.
        actor_label=None,
        old_value=old_value,
        new_value=new_value,
        reason=reason,
        ip_address=ip_address,
        # Chat-specific denormalized scope refs (kept for admin filters).
        channel_id=channel_id,
        subject_member_id=subject_member_id,
    )
    db.add(entry)
    await db.flush()
    return entry
