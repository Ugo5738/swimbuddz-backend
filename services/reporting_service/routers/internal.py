"""Internal service-to-service reporting endpoints.

These endpoints are called by background workers or other services,
not by frontend clients directly.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.reporting_service.services.aggregator import (
    compute_all_member_reports,
    compute_community_stats,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/reporting", tags=["internal"])


@router.post("/generate-snapshot")
async def trigger_snapshot_generation(
    year: int,
    quarter: int,
    caller: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Trigger snapshot generation for a quarter (called by ARQ worker)."""
    logger.info(f"Internal snapshot generation triggered for Q{quarter} {year}")
    count = await compute_all_member_reports(year, quarter, db)
    await compute_community_stats(year, quarter, db)
    return {"status": "completed", "member_count": count}
