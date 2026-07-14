"""Project Academy enrollment truth into members_service tier state."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_id, get_members_bulk, internal_post
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentStatus,
    PaymentStatus,
)

logger = get_logger(__name__)

_ACCESS_BEARING_STATUSES = (
    EnrollmentStatus.ENROLLED,
    EnrollmentStatus.PENDING_APPROVAL,
    EnrollmentStatus.DROPOUT_PENDING,
)


def _has_paid_access(row: Any) -> bool:
    return bool(
        row.payment_status == PaymentStatus.PAID
        or row.paid_at is not None
        or row.paid_installments_count > 0
    )


class AcademyProjectionError(RuntimeError):
    """Raised when members_service rejects an Academy projection update."""


async def latest_member_academy_end(
    db: AsyncSession,
    *,
    member_id: Any,
) -> datetime | None:
    """Return the latest end date across the member's active enrollments."""
    result = await db.execute(
        select(func.max(Cohort.end_date))
        .join(Enrollment, Enrollment.cohort_id == Cohort.id)
        .where(
            Enrollment.member_id == member_id,
            Enrollment.status.in_(_ACCESS_BEARING_STATUSES),
            or_(
                Enrollment.payment_status == PaymentStatus.PAID,
                Enrollment.paid_at.is_not(None),
                Enrollment.paid_installments_count > 0,
            ),
        )
    )
    return result.scalar_one_or_none()


async def send_member_academy_projection(
    *,
    member_auth_id: str,
    paid_until: datetime | None,
    source_reference: str,
) -> None:
    """Send an exact, naturally idempotent projection to members_service."""
    settings = get_settings()
    response = await internal_post(
        service_url=settings.MEMBERS_SERVICE_URL,
        path=f"/admin/members/by-auth/{member_auth_id}/academy/project",
        calling_service="academy",
        json={
            "paid_until": paid_until.isoformat() if paid_until else None,
            "source_reference": source_reference,
        },
    )
    if response.status_code >= 400:
        raise AcademyProjectionError(
            "members_service rejected Academy projection "
            f"for {member_auth_id} ({response.status_code}): {response.text}"
        )


async def project_member_academy_membership(
    db: AsyncSession,
    *,
    member_id: Any,
    member_auth_id: str | None,
    source_reference: str,
) -> datetime | None:
    """Compute and send one member's exact Academy entitlement projection."""
    resolved_auth_id = member_auth_id
    if not resolved_auth_id:
        member = await get_member_by_id(str(member_id), calling_service="academy")
        resolved_auth_id = member.get("auth_id") if member else None
    if not resolved_auth_id:
        raise AcademyProjectionError(
            f"Could not resolve auth ID for Academy member {member_id}"
        )

    paid_until = await latest_member_academy_end(db, member_id=member_id)
    await send_member_academy_projection(
        member_auth_id=resolved_auth_id,
        paid_until=paid_until,
        source_reference=source_reference,
    )
    return paid_until


async def reconcile_member_academy_memberships(db: AsyncSession) -> tuple[int, int]:
    """Rebuild all known Academy projections; return ``(updated, failed)``."""
    result = await db.execute(
        select(
            Enrollment.member_id,
            Enrollment.member_auth_id,
            Enrollment.status,
            Enrollment.payment_status,
            Enrollment.paid_at,
            Enrollment.paid_installments_count,
            Cohort.end_date,
        ).outerjoin(Cohort, Enrollment.cohort_id == Cohort.id)
    )
    rows = result.all()

    missing_member_ids = {str(row.member_id) for row in rows if not row.member_auth_id}
    resolved_auth_ids: dict[str, str] = {}
    if missing_member_ids:
        members = await get_members_bulk(
            sorted(missing_member_ids),
            calling_service="academy",
        )
        resolved_auth_ids = {
            str(member["id"]): member["auth_id"]
            for member in members
            if member.get("id") and member.get("auth_id")
        }

    projections: dict[str, datetime | None] = {}
    for row in rows:
        auth_id = row.member_auth_id or resolved_auth_ids.get(str(row.member_id))
        if not auth_id:
            logger.warning(
                "Skipping Academy projection without auth ID: member=%s",
                row.member_id,
            )
            continue
        projections.setdefault(auth_id, None)
        if (
            row.status not in _ACCESS_BEARING_STATUSES
            or not _has_paid_access(row)
            or row.end_date is None
        ):
            continue
        current = projections[auth_id]
        if current is None or row.end_date > current:
            projections[auth_id] = row.end_date

    updated = 0
    failed = 0
    for auth_id, paid_until in projections.items():
        try:
            await send_member_academy_projection(
                member_auth_id=auth_id,
                paid_until=paid_until,
                source_reference="academy-hourly-reconciliation",
            )
            updated += 1
        except Exception:
            failed += 1
            logger.warning(
                "Academy membership reconciliation failed for auth_id=%s",
                auth_id,
                exc_info=True,
            )

    return updated, failed
