"""Periodic chat-channel reconciliation for pods + location channels.

Implements the "derived-membership safety net" described in
CHAT_SERVICE_DESIGN.md §4.2 for the two parent types this service owns:

* **Pod** — walks every ACTIVE pod and re-asserts its chat channel +
  every PodAssignment where ``left_at IS NULL``.
* **Location** — walks every distinct ``Member.city``, ensures the
  corresponding location channel exists, and re-asserts membership for
  every member currently set to that city.

Mirrors ``services/academy_service/services/chat_reconciliation.py`` —
same shape, different parent entities. Add-only for now; subtract-side
needs a chat-service list endpoint that does not exist yet.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.common.logging import get_logger
from services.members_service.models import (
    MemberProfile,
    Pod,
    PodAssignment,
    PodStatus,
)
from services.members_service.services.chat_sync import (
    ensure_location_channel,
    ensure_pod_channel,
    reconcile_location_membership,
    reconcile_pod_membership,
)

logger = get_logger(__name__)


async def reconcile_pod_chat_memberships(db: AsyncSession) -> dict[str, int]:
    """Re-assert chat channels + memberships for every ACTIVE pod.

    Idempotent; safe on a schedule and on demand.
    """
    pods_stmt = (
        select(Pod)
        .options(selectinload(Pod.assignments))
        .where(Pod.status == PodStatus.ACTIVE)
    )
    pods = (await db.execute(pods_stmt)).scalars().all()

    members_attempted = 0
    members_failed = 0

    for pod in pods:
        await ensure_pod_channel(
            pod_id=pod.id,
            pod_name=pod.name,
            pod_lead_id=pod.pod_lead_id,
        )

        active_assignments = [a for a in pod.assignments if a.left_at is None]
        for assignment in active_assignments:
            members_attempted += 1
            ok = await reconcile_pod_membership(
                pod_id=pod.id,
                member_id=assignment.member_id,
                assignment_id=assignment.id,
                action="add",
            )
            if not ok:
                members_failed += 1

    counters = {
        "pods_scanned": len(pods),
        "members_attempted": members_attempted,
        "members_failed": members_failed,
    }
    logger.info("pod chat reconciliation: %s", counters)
    return counters


async def reconcile_location_chat_memberships(db: AsyncSession) -> dict[str, int]:
    """Re-assert chat channels + memberships for every active city.

    Walks every distinct non-null ``MemberProfile.city``, ensures a
    location channel exists, and adds every member with that city to it.
    Add-only; subtract-side waits for a chat-service list endpoint.

    The ``city`` field lives on ``MemberProfile`` (not ``Member``), so we
    walk profiles directly and read ``member_id`` from the join row.
    """
    profiles = (
        (
            await db.execute(
                select(MemberProfile.member_id, MemberProfile.city).where(
                    MemberProfile.city.isnot(None)
                )
            )
        )
        .all()
    )

    by_city: dict[str, list] = {}
    for member_id, city in profiles:
        if not city or not city.strip():
            continue
        by_city.setdefault(city.strip(), []).append(member_id)

    locations_ensured = 0
    members_attempted = 0
    members_failed = 0

    for city, member_ids in by_city.items():
        await ensure_location_channel(city=city)
        locations_ensured += 1

        for member_id in member_ids:
            members_attempted += 1
            ok = await reconcile_location_membership(
                city=city,
                member_id=member_id,
                action="add",
            )
            if not ok:
                members_failed += 1

    counters = {
        "profiles_scanned": len(profiles),
        "locations_ensured": locations_ensured,
        "members_attempted": members_attempted,
        "members_failed": members_failed,
    }
    logger.info("location chat reconciliation: %s", counters)
    return counters
