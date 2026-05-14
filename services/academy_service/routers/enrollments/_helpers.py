"""Private helpers for the enrollments router package.

Used by `me.withdraw_my_enrollment` to (a) annotate paid payments with the
refund obligation in payments_service metadata, and (b) recompute a
member's `academy_paid_until` from remaining ENROLLED cohorts.
"""

from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post
from services.academy_service.models import Cohort, Enrollment, EnrollmentStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


async def _annotate_payment_with_refund(
    *,
    payment_reference: str,
    refund_kobo: int,
    enrollment_id: str,
    window: str,
    reason: Optional[str],
    calling_service: str,
) -> None:
    """Best-effort: annotate a payment with refund obligation in its metadata.

    Calls payments_service internal endpoint. If it fails, the withdrawal
    still completes — admins can manually reconcile from the academy-side
    record (enrollment + installments).
    """
    try:
        _settings = get_settings()
        await internal_post(
            service_url=_settings.PAYMENTS_SERVICE_URL,
            path=f"/internal/payments/{payment_reference}/annotate-refund",
            calling_service=calling_service,
            json={
                "refund_kobo": refund_kobo,
                "enrollment_id": enrollment_id,
                "window": window,
                "reason": reason,
            },
        )
    except Exception:
        logger.warning(
            "Failed to annotate payment %s with refund obligation (best-effort)",
            payment_reference,
            exc_info=True,
        )


async def _recompute_member_academy_until(
    *,
    member_auth_id: str,
    member_id,
    db: AsyncSession,
) -> None:
    """After a withdrawal, recompute academy_paid_until from remaining cohorts.

    Multi-cohort safe: the member may be enrolled in other cohorts whose end
    dates extend past the withdrawn one. We pick the LATEST end_date across
    all remaining ENROLLED enrollments and call the existing academy/activate
    endpoint (which is idempotent and keeps the later of stored/supplied).

    If no remaining enrolled cohorts exist, expire academy access immediately
    by setting academy_paid_until to now — handled by a direct call to a
    members_service helper since the activate endpoint never shrinks.
    """
    remaining_query = (
        select(Cohort.end_date)
        .join(Enrollment, Enrollment.cohort_id == Cohort.id)
        .where(
            Enrollment.member_id == member_id,
            Enrollment.status == EnrollmentStatus.ENROLLED,
        )
        .order_by(Cohort.end_date.desc())
    )
    result = await db.execute(remaining_query)
    latest_end = result.scalar()

    _settings = get_settings()
    if latest_end:
        # Reuse academy/activate (idempotent, keeps later date)
        try:
            await internal_post(
                service_url=_settings.MEMBERS_SERVICE_URL,
                path=f"/admin/members/by-auth/{member_auth_id}/academy/activate",
                calling_service="academy",
                json={"cohort_end_date": latest_end.isoformat()},
            )
        except Exception:
            logger.warning(
                "Failed to refresh academy_paid_until after withdrawal "
                "(best-effort) for auth_id=%s",
                member_auth_id,
                exc_info=True,
            )
    else:
        # No remaining enrolled cohorts — expire academy access now.
        try:
            await internal_post(
                service_url=_settings.MEMBERS_SERVICE_URL,
                path=f"/admin/members/by-auth/{member_auth_id}/academy/expire",
                calling_service="academy",
                json={},
            )
        except Exception:
            logger.warning(
                "Failed to expire academy access after withdrawal "
                "(best-effort) for auth_id=%s",
                member_auth_id,
                exc_info=True,
            )
