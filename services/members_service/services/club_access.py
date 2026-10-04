"""Authoritative Club product access checks.

New Club purchases create location-specific :class:`ClubEnrollment` rows.
Those dated enrollments — rather than the legacy tier hierarchy — decide
whether a member can use a Club session.  The two explicitly supported
compatibility paths remain the old ``club_paid_until`` entitlement and the
post-Academy Club bridge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.members_service.models import (
    Club,
    ClubEnrollment,
    ClubPlanVersion,
    MemberMembership,
    Pod,
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _paid_until_covers(value: datetime | None, at: datetime) -> bool:
    return value is not None and _aware(value) > _aware(at)


async def resolve_club_access_checks(
    db: AsyncSession,
    checks: Iterable[Any],
) -> list[dict[str, Any]]:
    """Resolve many member/session access checks with bounded database work.

    Each check supplies ``context_key``, ``member_id``, ``at`` and optional
    ``club_id``/``pool_id``/``pod_id`` attributes. ``club_id`` is authoritative
    for the member's home entitlement. A host session may explicitly allow a
    member with an active enrollment at another Club to visit; that produces
    ``source=club_visit`` without changing the home enrollment. Pod and pool
    rules remain local to the host session. ``at`` is the session start, which
    is essential: buying Q4 in Q3 must not grant Q3 access.
    """

    requested = list(checks)
    if not requested:
        return []

    member_ids = {check.member_id for check in requested}
    earliest = min(_aware(check.at) for check in requested)
    latest = max(_aware(check.at) for check in requested)

    membership_rows = (
        (
            await db.execute(
                select(MemberMembership).where(
                    MemberMembership.member_id.in_(member_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    memberships = {row.member_id: row for row in membership_rows}

    enrollment_rows = (
        await db.execute(
            select(ClubEnrollment, Club, ClubPlanVersion)
            .join(Club, Club.id == ClubEnrollment.club_id)
            .join(
                ClubPlanVersion,
                ClubPlanVersion.id == ClubEnrollment.plan_version_id,
            )
            .where(
                ClubEnrollment.member_id.in_(member_ids),
                ClubEnrollment.status == "active",
                ClubEnrollment.starts_at <= latest,
                ClubEnrollment.ends_at > earliest,
                Club.is_active.is_(True),
            )
        )
    ).all()
    enrollments_by_member: dict[
        Any, list[tuple[ClubEnrollment, Club, ClubPlanVersion]]
    ] = {}
    for enrollment, club, plan in enrollment_rows:
        enrollments_by_member.setdefault(enrollment.member_id, []).append(
            (enrollment, club, plan)
        )

    pod_ids = {check.pod_id for check in requested if check.pod_id is not None}
    pod_club_ids: dict[Any, Any] = {}
    if pod_ids:
        pod_club_ids = {
            pod_id: club_id
            for pod_id, club_id in (
                await db.execute(select(Pod.id, Pod.club_id).where(Pod.id.in_(pod_ids)))
            ).all()
        }

    resolved: list[dict[str, Any]] = []
    for check in requested:
        at = _aware(check.at)
        check_club_id = getattr(check, "club_id", None)
        matched_enrollments: list[ClubEnrollment] = []
        access_mode = getattr(check, "club_access_mode", "plan_included")
        for enrollment, club, plan in enrollments_by_member.get(check.member_id, []):
            if not (_aware(enrollment.starts_at) <= at < _aware(enrollment.ends_at)):
                continue
            if check_club_id is not None:
                if check_club_id != enrollment.club_id:
                    continue
            if check.pod_id is not None:
                if pod_club_ids.get(check.pod_id) != enrollment.club_id:
                    continue
            session_id = getattr(check, "session_id", None)
            links = getattr(plan, "session_links", [])
            scheduled_quarter = (
                links
                and access_mode == "plan_included"
                and getattr(enrollment, "payment_mode", "quarterly_prepaid")
                == "quarterly_prepaid"
                and session_id is not None
            )
            if scheduled_quarter:
                # A location plan may explicitly include a visit to another pool.
                # Date overlap alone never includes an unsold extra session.
                if not any(
                    link.session_id == session_id
                    and (check.pool_id is None or link.pool_id == check.pool_id)
                    for link in links
                ):
                    continue
            elif check.pool_id is not None and not (
                check_club_id is not None
                and not links
                and getattr(enrollment, "payment_mode", "quarterly_prepaid")
                == "quarterly_prepaid"
            ):
                # Historical prepaid plans without an inclusion ledger retain
                # explicit Club ownership when Admin overrides a swim's pool.
                # Scheduled quarters and transition/extra-practice pricing
                # still respect their purchased inclusion/location snapshot.
                # Use the immutable commercial snapshot. The Club default is
                # only a fallback for historical plans created before the
                # snapshot columns existed.
                enrollment_pool_id = (
                    getattr(enrollment, "pool_id", None)
                    or plan.pool_id
                    or club.default_pool_id
                )
                if enrollment_pool_id != check.pool_id:
                    continue
            matched_enrollments.append(enrollment)

        # A prepaid quarter covering the same session takes precedence over a
        # temporary transition enrollment because its session price is included.
        matched_enrollment = next(
            (
                item
                for item in matched_enrollments
                if getattr(item, "payment_mode", "quarterly_prepaid")
                == "quarterly_prepaid"
            ),
            matched_enrollments[0] if matched_enrollments else None,
        )

        # Cross-location access is deliberate rather than a weakening of the
        # home-Club boundary. Only a real, dated ClubEnrollment can qualify as
        # a visitor; legacy generic entitlements and post-Academy bridges do not
        # silently become global Club passes.
        visiting_enrollment = None
        if (
            matched_enrollment is None
            and check_club_id is not None
            and bool(getattr(check, "allows_visiting_club_members", False))
        ):
            visit_candidates = [
                enrollment
                for enrollment, _club, _plan in enrollments_by_member.get(
                    check.member_id, []
                )
                if _aware(enrollment.starts_at) <= at < _aware(enrollment.ends_at)
                and enrollment.club_id != check_club_id
            ]
            visiting_enrollment = next(
                (
                    item
                    for item in visit_candidates
                    if getattr(item, "payment_mode", "quarterly_prepaid")
                    == "quarterly_prepaid"
                ),
                visit_candidates[0] if visit_candidates else None,
            )

        membership = memberships.get(check.member_id)
        if matched_enrollment is not None:
            payment_mode = getattr(
                matched_enrollment, "payment_mode", "quarterly_prepaid"
            )
            resolved.append(
                {
                    "context_key": check.context_key,
                    "allowed": True,
                    "source": (
                        "club_transition"
                        if payment_mode == "transition_per_session"
                        else "club_paid_addon"
                        if access_mode == "paid_addon"
                        else "club_enrollment"
                    ),
                    "enrollment_id": matched_enrollment.id,
                    "club_id": matched_enrollment.club_id,
                    "payment_mode": payment_mode,
                    # Members-service owns dated/location eligibility. The
                    # sessions service owns the current per-session price.
                    "fee_amount_kobo": 0
                    if payment_mode == "quarterly_prepaid"
                    and access_mode != "paid_addon"
                    else None,
                }
            )
        elif visiting_enrollment is not None:
            resolved.append(
                {
                    "context_key": check.context_key,
                    "allowed": True,
                    "source": "club_visit",
                    "enrollment_id": visiting_enrollment.id,
                    # This remains the member's home Club. The host Club is the
                    # check/session club_id and is deliberately not written onto
                    # the entitlement.
                    "club_id": visiting_enrollment.club_id,
                    "payment_mode": getattr(
                        visiting_enrollment, "payment_mode", "quarterly_prepaid"
                    ),
                    # Sessions owns the host location's visitor/add-on price.
                    "fee_amount_kobo": None,
                }
            )
        elif membership and _paid_until_covers(membership.post_academy_club_until, at):
            resolved.append(
                {
                    "context_key": check.context_key,
                    "allowed": True,
                    "source": "post_academy_bridge",
                    "enrollment_id": None,
                    "club_id": None,
                    "payment_mode": None,
                    "fee_amount_kobo": None,
                }
            )
        elif (
            membership
            and not enrollments_by_member.get(check.member_id)
            and _paid_until_covers(membership.club_paid_until, at)
        ):
            resolved.append(
                {
                    "context_key": check.context_key,
                    "allowed": True,
                    "source": "legacy_club_entitlement",
                    "enrollment_id": None,
                    "club_id": None,
                    "payment_mode": None,
                    "fee_amount_kobo": None,
                }
            )
        else:
            resolved.append(
                {
                    "context_key": check.context_key,
                    "allowed": False,
                    "source": "none",
                    "enrollment_id": None,
                    "club_id": None,
                    "payment_mode": None,
                    "fee_amount_kobo": None,
                }
            )

    return resolved


async def has_current_club_access(
    db: AsyncSession,
    *,
    member_id: Any,
    at: datetime,
) -> bool:
    """Return whether a member has any Club access at ``at``."""

    class _Check:
        context_key = "current"
        club_id = None
        pool_id = None
        pod_id = None

        def __init__(self) -> None:
            self.member_id = member_id
            self.at = at

    result = await resolve_club_access_checks(db, [_Check()])
    return bool(result and result[0]["allowed"])


async def current_club_enrollment_until(
    db: AsyncSession,
    *,
    member_id: Any,
    at: datetime,
) -> datetime | None:
    """Return the latest end of a location-specific enrollment active now."""

    current = _aware(at)
    return (
        await db.execute(
            select(func.max(ClubEnrollment.ends_at)).where(
                ClubEnrollment.member_id == member_id,
                ClubEnrollment.status == "active",
                ClubEnrollment.starts_at <= current,
                ClubEnrollment.ends_at > current,
            )
        )
    ).scalar_one_or_none()
