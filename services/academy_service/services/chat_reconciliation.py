"""Periodic chat-channel reconciliation for academy cohorts.

Implements the "derived-membership safety net" described in
CHAT_SERVICE_DESIGN.md §4.2: walks every cohort with active enrollments and
re-asserts the corresponding chat channel + member rows. Every chat-sync
call in this codebase is best-effort (see ``services/chat_sync.py`` — a
``try/except`` around the HTTP call that logs a warning and continues).
Without a reconciliation pass, any transient chat-service outage during a
hook leaves permanently-orphaned enrollments. The same pass also heals
gaps from new code paths that forget to call chat_sync.

Currently add-only: enrollments that should be in the channel get added.
Removing stale members (e.g. dropped enrollments left in the channel)
requires a list endpoint on chat_service that does not exist yet and is
deferred — wrong-add errs on the side of access, which is recoverable;
the wrong-remove direction is harder to spot.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.common.logging import get_logger
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
)
from services.academy_service.services.chat_sync import (
    ensure_cohort_channel,
    reconcile_cohort_membership,
)

logger = get_logger(__name__)


async def reconcile_cohort_chat_memberships(db: AsyncSession) -> dict[str, int]:
    """Re-assert chat channels + memberships for every active enrollment.

    Returns a small counters dict for logging/observability.

    Safe to invoke on a schedule and on demand (admin endpoint). All
    underlying calls are idempotent: ``ensure_cohort_channel`` returns the
    existing channel id on subsequent invocations, ``reconcile_cohort_membership``
    with ``action="add"`` is a no-op when the member is already in the
    channel.
    """
    stmt = (
        select(Enrollment)
        .options(selectinload(Enrollment.cohort))
        .where(
            Enrollment.status == EnrollmentStatus.ENROLLED,
            Enrollment.cohort_id.isnot(None),
        )
    )
    enrollments = (await db.execute(stmt)).scalars().all()

    cohorts_ensured: set = set()
    members_attempted = 0
    members_failed = 0

    for enr in enrollments:
        cohort: Cohort | None = enr.cohort
        if cohort is None:
            continue

        if cohort.id not in cohorts_ensured:
            await ensure_cohort_channel(
                cohort_id=cohort.id,
                cohort_name=cohort.name,
                created_by_member_id=cohort.coach_id,
            )
            cohorts_ensured.add(cohort.id)

        members_attempted += 1
        ok = await reconcile_cohort_membership(
            cohort_id=cohort.id,
            member_id=enr.member_id,
            enrollment_id=enr.id,
            action="add",
        )
        if not ok:
            members_failed += 1

    counters = {
        "enrollments_scanned": len(enrollments),
        "cohorts_ensured": len(cohorts_ensured),
        "members_attempted": members_attempted,
        "members_failed": members_failed,
    }
    logger.info("chat reconciliation: %s", counters)
    return counters
