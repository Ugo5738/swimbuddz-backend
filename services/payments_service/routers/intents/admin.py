"""Admin payment endpoints.

* DELETE /payments/admin/members/by-auth/{auth_id} — purge all
  payments for a member (used by GDPR / test cleanup tooling).
* POST /payments/admin/{reference}/replay-entitlement — re-run
  entitlement fulfillment for a PAID payment whose previous apply
  failed (used during incident response).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.payments_service.models import (
    Payment,
    PaymentStatus,
)
from services.payments_service.schemas import (
    PaymentResponse,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._entitlement import _apply_entitlement_with_tracking

router = APIRouter()


@router.delete("/admin/members/by-auth/{auth_id}")
async def admin_delete_member_payments(
    auth_id: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Delete payments for a member by auth ID (Admin only).
    """
    result = await db.execute(delete(Payment).where(Payment.member_auth_id == auth_id))
    await db.commit()
    return {"deleted": result.rowcount or 0}


@router.post("/admin/{reference}/replay-entitlement", response_model=PaymentResponse)
async def replay_payment_entitlement(
    reference: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Replay entitlement fulfillment for a paid payment."""
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found"
        )
    if payment.status != PaymentStatus.PAID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment is not paid (status={payment.status.value})",
        )

    await _apply_entitlement_with_tracking(payment)
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    logger.info(
        "Entitlement replay requested by %s for payment %s",
        current_user.user_id,
        reference,
    )
    return payment
