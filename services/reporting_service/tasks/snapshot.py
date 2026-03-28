"""Quarterly snapshot generation task.

Called by the ARQ worker to compute reports for the most recently completed quarter.
"""

from datetime import datetime

from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import AsyncSessionLocal
from services.reporting_service.models import QuarterlySnapshot, ReportStatus
from services.reporting_service.services.aggregator import (
    compute_all_member_reports,
    compute_community_stats,
)
from services.reporting_service.services.quarter_utils import LAGOS_TZ, current_quarter

logger = get_logger(__name__)


async def _send_report_emails(year: int, quarter: int, db) -> None:
    """Send quarterly report emails to all members after generation."""
    import httpx

    from libs.common.config import settings
    from libs.common.emails.client import get_email_client
    from services.reporting_service.models import MemberQuarterlyReport

    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    reports = result.scalars().all()
    email_client = get_email_client()
    frontend_url = getattr(settings, "FRONTEND_URL", "https://swimbuddz.com")
    members_url = getattr(
        settings, "MEMBERS_SERVICE_URL", "http://members-service:8001"
    )
    sent = 0

    for report in reports:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{members_url}/members/by-auth-id/{report.member_auth_id}",
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                member_email = resp.json().get("email")
                if not member_email:
                    continue

            report_url = f"{frontend_url}/account/reports/q{quarter}-{year}"
            await email_client.send_template(
                template_type="quarterly_report",
                to_email=member_email,
                template_data={
                    "member_name": report.member_name,
                    "year": year,
                    "quarter": quarter,
                    "sessions_attended": report.total_sessions_attended,
                    "attendance_rate": report.attendance_rate,
                    "streak_longest": report.streak_longest,
                    "milestones_achieved": report.milestones_achieved,
                    "bubbles_earned": report.bubbles_earned,
                    "volunteer_hours": report.volunteer_hours,
                    "total_spent_ngn": report.total_spent_ngn,
                    "report_url": report_url,
                },
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Email failed for {report.member_name}: {e}")

    logger.info(
        f"Sent {sent}/{len(reports)} quarterly report emails for Q{quarter} {year}"
    )


def _previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return the (year, quarter) for the previous quarter."""
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


async def run_quarterly_snapshot():
    """Compute and store the quarterly snapshot for the most recently completed quarter."""
    now = datetime.now(LAGOS_TZ)
    cur_year, cur_quarter = current_quarter(now)
    # We want to snapshot the *previous* quarter
    year, quarter = _previous_quarter(cur_year, cur_quarter)

    logger.info(f"Starting quarterly snapshot for Q{quarter} {year}")

    async with AsyncSessionLocal() as db:
        # Check if already completed
        result = await db.execute(
            select(QuarterlySnapshot).where(
                QuarterlySnapshot.year == year,
                QuarterlySnapshot.quarter == quarter,
            )
        )
        existing = result.scalar_one_or_none()

        if existing and existing.status == ReportStatus.COMPLETED:
            logger.info(f"Snapshot for Q{quarter} {year} already completed. Skipping.")
            return

        # Create or update snapshot record
        if existing:
            snapshot = existing
            snapshot.status = ReportStatus.COMPUTING
            snapshot.started_at = utc_now()
            snapshot.error_message = None
        else:
            snapshot = QuarterlySnapshot(
                year=year,
                quarter=quarter,
                status=ReportStatus.COMPUTING,
                started_at=utc_now(),
            )
            db.add(snapshot)

        await db.commit()
        await db.refresh(snapshot)

        try:
            # Compute all member reports
            count = await compute_all_member_reports(year, quarter, db)

            # Compute community stats from the member reports
            await compute_community_stats(year, quarter, db)

            # Mark as completed
            snapshot.status = ReportStatus.COMPLETED
            snapshot.member_count = count
            snapshot.completed_at = utc_now()
            await db.commit()

            logger.info(
                f"Quarterly snapshot for Q{quarter} {year} completed. "
                f"{count} member reports generated."
            )

            # Send report emails to all members
            try:
                await _send_report_emails(year, quarter, db)
            except Exception as email_err:
                logger.error(f"Failed to send report emails: {email_err}")
                # Don't fail the whole snapshot for email issues

        except Exception as e:
            logger.error(f"Snapshot generation failed for Q{quarter} {year}: {e}")
            snapshot.status = ReportStatus.FAILED
            snapshot.error_message = str(e)[:500]
            await db.commit()
            raise
