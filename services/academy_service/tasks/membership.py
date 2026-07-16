"""Background repair for Academy membership projections."""

from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.academy_service.services.membership_projection import (
    reconcile_member_academy_memberships,
)

logger = get_logger(__name__)


async def reconcile_academy_membership_projections() -> None:
    """Re-assert members_service Academy state from enrollment truth."""
    async for db in get_async_db():
        try:
            updated, failed = await reconcile_member_academy_memberships(db)
            logger.info(
                "Academy membership reconciliation complete: updated=%d failed=%d",
                updated,
                failed,
            )
        except Exception:
            logger.error(
                "Academy membership reconciliation failed",
                exc_info=True,
            )
        finally:
            await db.close()
            break
