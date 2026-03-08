"""Admin supplier payout management routes."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.store_service.models import (
    AuditEntityType,
    PayoutStatus,
    Supplier,
    SupplierPayout,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    SupplierPayoutCreate,
    SupplierPayoutListResponse,
    SupplierPayoutResponse,
    SupplierPayoutStatusUpdate,
)

router = APIRouter(tags=["admin-payouts"])


# ---------------------------------------------------------------------------
# LIST PAYOUTS FOR A SUPPLIER
# ---------------------------------------------------------------------------


@router.get(
    "/suppliers/{supplier_id}/payouts",
    response_model=SupplierPayoutListResponse,
)
async def list_supplier_payouts(
    supplier_id: uuid.UUID,
    status_filter: Optional[PayoutStatus] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List payouts for a specific supplier."""
    # Verify supplier exists
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    query = select(SupplierPayout).where(SupplierPayout.supplier_id == supplier_id)

    if status_filter is not None:
        query = query.where(SupplierPayout.status == status_filter)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(SupplierPayout.payout_period_end.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    payouts = (await db.execute(query)).scalars().all()

    return SupplierPayoutListResponse(
        items=payouts,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# CREATE PAYOUT
# ---------------------------------------------------------------------------


@router.post(
    "/suppliers/{supplier_id}/payouts",
    response_model=SupplierPayoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payout(
    supplier_id: uuid.UUID,
    payout_in: SupplierPayoutCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a payout record for a supplier."""
    # Verify supplier exists
    supplier = (
        await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    ).scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    if payout_in.payout_period_end < payout_in.payout_period_start:
        raise HTTPException(
            status_code=400,
            detail="Payout period end must be after start",
        )

    payout = SupplierPayout(
        supplier_id=supplier_id,
        **payout_in.model_dump(),
    )
    db.add(payout)
    await db.commit()
    await db.refresh(payout)

    await log_audit(
        db,
        AuditEntityType.SUPPLIER_PAYOUT,
        payout.id,
        "created",
        current_user.user_id,
        new_value={
            "supplier_id": str(supplier_id),
            "period": f"{payout_in.payout_period_start} - {payout_in.payout_period_end}",
            "payout_amount_ngn": str(payout_in.payout_amount_ngn),
        },
    )
    await db.commit()

    return payout


# ---------------------------------------------------------------------------
# UPDATE PAYOUT STATUS
# ---------------------------------------------------------------------------


@router.patch(
    "/payouts/{payout_id}/status",
    response_model=SupplierPayoutResponse,
)
async def update_payout_status(
    payout_id: uuid.UUID,
    status_in: SupplierPayoutStatusUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a payout's status (e.g., pending -> processing -> paid)."""
    result = await db.execute(
        select(SupplierPayout).where(SupplierPayout.id == payout_id)
    )
    payout = result.scalar_one_or_none()
    if not payout:
        raise HTTPException(status_code=404, detail="Payout not found")

    old_status = payout.status

    # Update status
    payout.status = status_in.status

    # Auto-set paid_at when status transitions to PAID
    if status_in.status == PayoutStatus.PAID and not payout.paid_at:
        payout.paid_at = utc_now()

    # Update payment reference if provided
    if status_in.payment_reference is not None:
        payout.payment_reference = status_in.payment_reference

    # Update notes if provided
    if status_in.notes is not None:
        payout.notes = status_in.notes

    await log_audit(
        db,
        AuditEntityType.SUPPLIER_PAYOUT,
        payout.id,
        "status_updated",
        current_user.user_id,
        old_value={"status": old_status.value},
        new_value={"status": status_in.status.value},
    )
    await db.commit()
    await db.refresh(payout)

    return payout
