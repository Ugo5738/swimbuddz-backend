"""Media adoption totals without returning private media or access links."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, reporting_window
from libs.db.session import get_async_db
from services.media_service.models import MediaItem, MediaVault

router = APIRouter(prefix="/internal/media", tags=["internal"])


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    original = [
        MediaItem.soft_deleted_at.is_(None),
        MediaItem.source_media_id.is_(None),
        MediaItem.duplicate_of_id.is_(None),
    ]
    uploaded = [MediaItem.created_at >= start, MediaItem.created_at < end, *original]
    row = (
        await db.execute(
            select(
                func.count(MediaItem.id).label("originals"),
                func.count(MediaItem.id)
                .filter(MediaItem.vault_id.is_not(None))
                .label("vault_originals"),
                func.count(func.distinct(MediaItem.vault_id)).label("populated_vaults"),
                func.count(func.distinct(MediaItem.uploaded_by)).label("contributors"),
            ).where(*uploaded)
        )
    ).one()
    vaults = (
        await db.execute(
            select(func.count(MediaVault.id)).where(
                MediaVault.created_at >= start,
                MediaVault.created_at < end,
                MediaVault.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    released = (
        await db.execute(
            select(func.count(MediaItem.id)).where(
                *original,
                MediaItem.published_at >= start,
                MediaItem.published_at < end,
            )
        )
    ).scalar_one()
    capture_dates = (
        await db.execute(
            select(func.count(MediaVault.id)).where(
                MediaVault.capture_date >= date_from.date(),
                MediaVault.capture_date <= date_to.date(),
                MediaVault.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    return ReportingSection(
        metrics={
            "vaults_created": vaults,
            "vaults_with_capture_date": capture_dates,
            "vaults_populated": row.populated_vaults,
            "original_assets_uploaded": row.originals,
            "vault_original_assets_uploaded": row.vault_originals,
            "media_contributors": row.contributors,
            "original_assets_published": released,
        },
        notes=[
            "Vault creation/capture dates do not prove filming. Populated vaults contain an original uploaded in the window. Derivatives, known duplicates and currently deleted assets are excluded.",
            "Publication counts use published_at, independently of upload date. Counts do not measure media downloads, views or consent decisions; no private URLs are returned.",
        ],
    )
