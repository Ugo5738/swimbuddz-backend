"""Internal service-to-service chat endpoints.

Called by upstream services (academy, sessions, events, transport) when their
derived state changes — e.g. academy posts to `/internal/chat/channels/ensure`
on `cohort.created`, then `/internal/chat/memberships/reconcile` on each
`enrollment.confirmed`. These endpoints require a service-role JWT and are
NOT proxied by the gateway.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db

from services.chat_service.schemas import (
    EnsureChannelRequest,
    EnsureChannelResponse,
    ReconcileAction,
    ReconcileMembershipRequest,
    ReconcileMembershipResponse,
)
from services.chat_service.services import channel_ops, membership_ops

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/chat", tags=["internal-chat"])


@router.post(
    "/channels/ensure",
    response_model=EnsureChannelResponse,
    status_code=status.HTTP_200_OK,
)
async def ensure_channel(
    body: EnsureChannelRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Idempotent create-or-fetch for a channel tied to a parent entity.

    Called once per parent — subsequent calls return the existing channel."""
    channel, created = await channel_ops.ensure_channel(
        db,
        type=body.type,
        parent_entity_type=body.parent_entity_type,
        parent_entity_id=body.parent_entity_id,
        name=body.name,
        retention_policy=body.retention_policy,
        description=body.description,
        created_by=body.created_by,
        safeguarding_flags=body.safeguarding_flags,
    )
    return EnsureChannelResponse(channel_id=channel.id, created=created)


@router.post(
    "/memberships/reconcile",
    response_model=ReconcileMembershipResponse,
)
async def reconcile_membership(
    body: ReconcileMembershipRequest,
    service_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Add or remove a member based on an upstream parent state change.

    Idempotent: re-adding an active member is a no-op (with role upgrade);
    re-removing a member who already left is a no-op."""
    channel = await membership_ops.resolve_channel(
        db,
        channel_id=body.channel_id,
        parent_entity_type=body.parent_entity_type,
        parent_entity_id=body.parent_entity_id,
    )

    # Service-role tokens have a synthetic `sub` like "service:academy" — not
    # a UUID — so we don't pass an actor_id for s2s actions.
    actor_id = None

    if body.action == ReconcileAction.ADD:
        membership = await membership_ops.add_member(
            db,
            channel=channel,
            member_id=body.member_id,
            role=body.role,
            derived_from=body.derived_from,
            derivation_ref=body.derivation_ref,
            actor_id=actor_id,
        )
        return ReconcileMembershipResponse(
            channel_id=channel.id,
            member_id=body.member_id,
            action_taken=ReconcileAction.ADD,
            role=membership.role,
        )

    await membership_ops.remove_member(
        db,
        channel=channel,
        member_id=body.member_id,
        actor_id=actor_id,
    )
    return ReconcileMembershipResponse(
        channel_id=channel.id,
        member_id=body.member_id,
        action_taken=ReconcileAction.REMOVE,
        role=body.role,  # echoed back for the response shape; not authoritative on remove
    )
