"""Admin-facing quarterly report endpoints."""

import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.models import (
    CommunityQuarterlyStats,
    MemberQuarterlyReport,
    QuarterlySnapshot,
    ReportStatus,
)
from services.reporting_service.schemas.business_review import BusinessReviewResponse
from services.reporting_service.schemas.reports import (
    CommunityQuarterlyStatsResponse,
    GenerateReportRequest,
    MemberQuarterlyReportResponse,
    SnapshotStatusResponse,
    QuarterlyReportSummary,
)
from services.reporting_service.services.business_review import build_business_review
from services.reporting_service.services.quarter_utils import quarter_date_range
from services.reporting_service.services.aggregator import (
    compute_all_member_reports,
    compute_community_stats,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])


@router.get("/quarterly/available", response_model=list[QuarterlyReportSummary])
async def admin_available_quarters(
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Completed/known quarterly snapshots, newest first, for the admin selector."""
    rows = (
        (
            await db.execute(
                select(QuarterlySnapshot).order_by(
                    QuarterlySnapshot.year.desc(),
                    QuarterlySnapshot.quarter.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        QuarterlyReportSummary(
            year=row.year,
            quarter=row.quarter,
            label=f"Q{row.quarter} {row.year}",
            status=row.status.value
            if hasattr(row.status, "value")
            else str(row.status),
            computed_at=row.completed_at,
        )
        for row in rows
    ]


@router.get("/quarterly/business-review", response_model=BusinessReviewResponse)
async def admin_business_review(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Decision-oriented quarter-end management review.

    This composes the frozen quarterly engagement snapshot with source-of-truth
    Academy, Club, session, and ledger summaries.
    """
    try:
        return await build_business_review(year, quarter, db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/quarterly/overview", response_model=CommunityQuarterlyStatsResponse)
async def admin_quarterly_overview(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin overview of community quarterly stats."""
    result = await db.execute(
        select(CommunityQuarterlyStats).where(
            CommunityQuarterlyStats.year == year,
            CommunityQuarterlyStats.quarter == quarter,
        )
    )
    stats = result.scalar_one_or_none()
    if stats is None:
        raise HTTPException(
            status_code=404, detail="No stats found. Generate the report first."
        )
    return stats


@router.get("/quarterly/members", response_model=list[MemberQuarterlyReportResponse])
async def admin_list_member_reports(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    sort: str = Query("attendance", regex="^(attendance|streak|milestones|name)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Paginated list of all member reports for a quarter."""
    sort_map = {
        "attendance": MemberQuarterlyReport.total_sessions_attended.desc(),
        "streak": MemberQuarterlyReport.streak_longest.desc(),
        "milestones": MemberQuarterlyReport.milestones_achieved.desc(),
        "name": MemberQuarterlyReport.member_name.asc(),
    }
    order = sort_map.get(sort, sort_map["attendance"])

    result = await db.execute(
        select(MemberQuarterlyReport)
        .where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
            func.lower(MemberQuarterlyReport.member_tier).in_(["academy", "club", "community"]),
        )
        .order_by(order)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/quarterly/export.csv")
async def admin_export_csv(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Export all member reports as CSV for a quarter."""
    result = await db.execute(
        select(MemberQuarterlyReport)
        .where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
            func.lower(MemberQuarterlyReport.member_tier).in_(["academy", "club", "community"]),
        )
        .order_by(MemberQuarterlyReport.member_name.asc())
    )
    reports = result.scalars().all()

    if not reports:
        raise HTTPException(status_code=404, detail="No reports found.")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Member Name",
            "Tier",
            "Sessions Attended",
            "Sessions Available",
            "Attendance Rate %",
            "Punctuality Rate %",
            "Longest Streak",
            "Milestones Achieved",
            "Certificates Earned",
            "Programs Enrolled",
            "Total Spent (NGN)",
            "Bubbles Earned",
            "Bubbles Spent",
            "Rides Taken",
            "Rides Offered",
            "Volunteer Hours",
            "Orders Placed",
            "Store Spent (NGN)",
            "Events Attended",
        ]
    )

    for r in reports:
        writer.writerow(
            [
                r.member_name,
                r.member_tier or "",
                r.total_sessions_attended,
                r.total_sessions_available,
                round(r.attendance_rate * 100, 1),
                round(r.punctuality_rate * 100, 1),
                r.streak_longest,
                r.milestones_achieved,
                r.certificates_earned,
                r.programs_enrolled,
                r.total_spent_ngn,
                r.bubbles_earned,
                r.bubbles_spent,
                r.rides_taken,
                r.rides_offered,
                round(r.volunteer_hours, 1),
                r.orders_placed,
                r.store_spent_ngn,
                r.events_attended,
            ]
        )

    output.seek(0)
    filename = f"swimbuddz-Q{quarter}-{year}-report.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/quarterly/generate", response_model=SnapshotStatusResponse)
async def admin_generate_report(
    body: GenerateReportRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Trigger quarterly report generation for a completed quarter."""
    _, quarter_end = quarter_date_range(body.year, body.quarter)
    if quarter_end >= utc_now():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Q{body.quarter} {body.year} has not ended yet. "
                "Quarter-end reports can only be generated after the quarter closes."
            ),
        )

    # Check if snapshot already exists
    result = await db.execute(
        select(QuarterlySnapshot).where(
            QuarterlySnapshot.year == body.year,
            QuarterlySnapshot.quarter == body.quarter,
        )
    )
    existing = result.scalar_one_or_none()

    if existing and existing.status == ReportStatus.COMPUTING:
        raise HTTPException(
            status_code=409, detail="Report generation already in progress."
        )

    # Create or update snapshot record
    if existing:
        existing.status = ReportStatus.COMPUTING
        existing.error_message = None
        snapshot = existing
    else:
        snapshot = QuarterlySnapshot(
            year=body.year,
            quarter=body.quarter,
            status=ReportStatus.COMPUTING,
        )
        db.add(snapshot)

    await db.commit()
    await db.refresh(snapshot)

    # Run computation (in a real production setup, this would be an ARQ background task)
    try:
        count = await compute_all_member_reports(body.year, body.quarter, db)
        await compute_community_stats(body.year, body.quarter, db)
        snapshot.status = ReportStatus.COMPLETED
        snapshot.member_count = count
        snapshot.semantics_version = 2
        snapshot.completed_at = utc_now()
        await db.commit()
        await db.refresh(snapshot)
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        snapshot.status = ReportStatus.FAILED
        snapshot.error_message = str(e)
        await db.commit()
        await db.refresh(snapshot)

    return snapshot


@router.get("/quarterly/status", response_model=SnapshotStatusResponse)
async def admin_report_status(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Check the status of a quarterly snapshot job."""
    result = await db.execute(
        select(QuarterlySnapshot).where(
            QuarterlySnapshot.year == year,
            QuarterlySnapshot.quarter == quarter,
        )
    )
    snapshot = result.scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail="No snapshot found. Trigger generation first.",
        )
    return snapshot


class EmailReportsRequest(BaseModel):
    year: int
    quarter: int


class EmailReportsResponse(BaseModel):
    sent: int
    failed: int
    skipped: int


@router.post("/quarterly/send-emails", response_model=EmailReportsResponse)
async def admin_send_report_emails(
    body: EmailReportsRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Send quarterly report emails to all members who have reports."""
    from libs.common.config import settings
    from libs.common.emails.client import get_email_client

    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.year == body.year,
            MemberQuarterlyReport.quarter == body.quarter,
            func.lower(MemberQuarterlyReport.member_tier).in_(["academy", "club", "community"]),
        )
    )
    reports = result.scalars().all()

    if not reports:
        raise HTTPException(
            status_code=404,
            detail="No reports found. Generate reports first.",
        )

    email_client = get_email_client()
    frontend_url = getattr(settings, "FRONTEND_URL", "https://swimbuddz.com")
    sent = 0
    failed = 0
    skipped = 0

    for report in reports:
        # Look up member email via members service
        try:
            import httpx

            members_url = getattr(
                settings, "MEMBERS_SERVICE_URL", "http://members-service:8001"
            )
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{members_url}/members/by-auth-id/{report.member_auth_id}",
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.warning(
                        f"Could not find email for member {report.member_auth_id}"
                    )
                    skipped += 1
                    continue
                member_data = resp.json()
                member_email = member_data.get("email")
                if not member_email:
                    skipped += 1
                    continue
        except Exception as e:
            logger.error(f"Error looking up member email: {e}")
            skipped += 1
            continue

        report_url = f"{frontend_url}/account/reports/" f"q{body.quarter}-{body.year}"

        try:
            await email_client.send_template(
                template_type="quarterly_report",
                to_email=member_email,
                template_data={
                    "member_name": report.member_name,
                    "year": body.year,
                    "quarter": body.quarter,
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
            logger.error(f"Failed to send report email to {member_email}: {e}")
            failed += 1

    return EmailReportsResponse(sent=sent, failed=failed, skipped=skipped)
