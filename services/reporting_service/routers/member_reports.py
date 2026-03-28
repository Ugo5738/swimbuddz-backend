"""Member-facing quarterly report endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.models import MemberQuarterlyReport
from services.reporting_service.schemas.reports import (
    MemberQuarterlyReportResponse,
    PrivacyToggleRequest,
    QuarterlyReportSummary,
)
from services.reporting_service.services.aggregator import compute_member_report
from services.reporting_service.services.card_generator import generate_card_image
from services.reporting_service.services.pdf_generator import generate_pdf_report
from services.reporting_service.services.quarter_utils import quarter_label

logger = get_logger(__name__)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/me/quarterly", response_model=MemberQuarterlyReportResponse)
async def get_my_quarterly_report(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the current member's quarterly report.

    If no pre-computed report exists, computes it on the fly for the current quarter.
    """
    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.member_auth_id == current_user.user_id,
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    report = result.scalar_one_or_none()

    if report is None:
        # Try real-time computation for the current/recent quarter
        try:
            report = await compute_member_report(
                member_auth_id=current_user.user_id,
                year=year,
                quarter=quarter,
                db=db,
            )
        except Exception as e:
            logger.error(f"Failed to compute report for {current_user.user_id}: {e}")
            raise HTTPException(
                status_code=404,
                detail="Report not available for this quarter.",
            )

    return report


@router.get("/me/quarterly/card")
async def get_my_quarterly_card(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    format: str = Query("square", regex="^(square|story)$"),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get the shareable card image for the current member's quarterly report."""
    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.member_auth_id == current_user.user_id,
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    report = result.scalar_one_or_none()

    if report is None:
        raise HTTPException(
            status_code=404, detail="Report not available for this quarter."
        )

    image_bytes = await generate_card_image(report, format=format)

    return Response(
        content=image_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'inline; filename="swimbuddz-{quarter_label(year, quarter)}'
                f'-{format}.png"'
            ),
            "Cache-Control": "public, max-age=86400",
        },
    )


@router.get("/me/quarterly/pdf")
async def get_my_quarterly_pdf(
    year: int = Query(..., ge=2025, le=2030),
    quarter: int = Query(..., ge=1, le=4),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Download a PDF version of the current member's quarterly report."""
    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.member_auth_id == current_user.user_id,
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    report = result.scalar_one_or_none()

    if report is None:
        try:
            report = await compute_member_report(
                member_auth_id=current_user.user_id,
                year=year,
                quarter=quarter,
                db=db,
            )
        except Exception as e:
            logger.error(f"Failed to compute report for PDF: {e}")
            raise HTTPException(
                status_code=404,
                detail="Report not available for this quarter.",
            )

    pdf_bytes = await generate_pdf_report(report)

    filename = f"swimbuddz-{quarter_label(year, quarter)}-report.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.put("/me/quarterly/privacy")
async def toggle_leaderboard_privacy(
    body: PrivacyToggleRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Toggle leaderboard opt-out for a specific quarter."""
    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.member_auth_id == current_user.user_id,
            MemberQuarterlyReport.year == body.year,
            MemberQuarterlyReport.quarter == body.quarter,
        )
    )
    report = result.scalar_one_or_none()

    if report is None:
        raise HTTPException(status_code=404, detail="Report not found.")

    report.leaderboard_opt_out = body.leaderboard_opt_out
    await db.commit()
    return {"status": "ok", "leaderboard_opt_out": report.leaderboard_opt_out}


@router.get("/quarterly/available", response_model=list[QuarterlyReportSummary])
async def list_available_quarters(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List quarters that have reports available for the current member."""
    result = await db.execute(
        select(MemberQuarterlyReport)
        .where(MemberQuarterlyReport.member_auth_id == current_user.user_id)
        .order_by(
            MemberQuarterlyReport.year.desc(),
            MemberQuarterlyReport.quarter.desc(),
        )
    )
    reports = result.scalars().all()

    return [
        QuarterlyReportSummary(
            year=r.year,
            quarter=r.quarter,
            label=quarter_label(r.year, r.quarter),
            status="completed",
            computed_at=r.computed_at,
        )
        for r in reports
    ]
