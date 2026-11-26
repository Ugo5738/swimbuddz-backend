"""Internal service-to-service endpoints for store-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db

router = APIRouter(prefix="/internal/store", tags=["internal"])


class StoreVerifiedPayment(BaseModel):
    """Already-verified settlement evidence from payments_service only."""

    payment_reference: str
    wallet_transaction_id: str | None = None
    paid_at: datetime | None = None


@router.post("/orders/{order_id}/confirm-payment", include_in_schema=False)
async def confirm_store_payment(
    order_id: uuid.UUID,
    payload: StoreVerifiedPayment,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply a trusted payment confirmation without regressing fulfillment."""
    from services.store_service.routers.admin_inventory.orders import (
        confirm_verified_order_payment,
    )

    return await confirm_verified_order_payment(
        order_id,
        payment_reference=payload.payment_reference,
        wallet_transaction_id=payload.wallet_transaction_id,
        paid_at=payload.paid_at,
        current_user=current_user,
        db=db,
    )


class MemberStoreSummary(BaseModel):
    orders_placed: int = 0
    total_spent: int = 0


@router.get(
    "/member-summary/{member_auth_id}",
    response_model=MemberStoreSummary,
)
async def get_member_store_summary(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate store order stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    """
    from services.store_service.models import Order

    result = await db.execute(
        select(
            func.count(Order.id).label("count"),
            func.coalesce(func.sum(Order.total_ngn), 0).label("total"),
        ).where(
            Order.member_auth_id == member_auth_id,
            Order.status.in_(["paid", "processing", "shipped", "delivered"]),
            Order.paid_at >= date_from,
            Order.paid_at <= date_to,
        )
    )
    row = result.one()

    return MemberStoreSummary(
        orders_placed=row.count or 0,
        total_spent=int(row.total or 0),
    )


@router.get("/financial-obligations/{member_auth_id}")
async def member_store_unfinished_orders(
    member_auth_id: str,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Unfinished orders are OPTIONAL checkouts, not automatically member debt."""
    from services.store_service.models import Order, OrderStatus

    orders = (
        (
            await db.execute(
                select(Order)
                .where(Order.member_auth_id == member_auth_id)
                .where(
                    Order.status.in_(
                        [OrderStatus.PENDING_PAYMENT, OrderStatus.PAYMENT_FAILED]
                    )
                )
                .order_by(Order.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(order.id),
            "order_number": order.order_number,
            "title": f"Store order {order.order_number}",
            "amount_kobo": int(order.total_ngn * 100),
            "status": order.status.value,
            "created_at": order.created_at.isoformat(),
        }
        for order in orders
    ]


@router.get("/finance-summary", dependencies=[Depends(require_service_role)])
async def store_finance_summary(db: AsyncSession = Depends(get_async_db)):
    """Service-owned Store operational finance summary for gateway aggregation."""
    from services.store_service.models import InventoryItem, Order
    from services.store_service.models.procurement import (
        StoreCostEvent,
        StoreInventoryValuation,
        StoreSupplierInvoice,
        StoreTradeReceivable,
    )

    store_rows = (
        await db.execute(
            select(
                Order.status,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_ngn), 0),
            ).group_by(Order.status)
        )
    ).all()
    order_status_totals = [
        {
            "status": status.value if hasattr(status, "value") else str(status),
            "count": int(count),
            "gross_order_ngn": str(total),
        }
        for status, count, total in store_rows
    ]

    valued_minor, unvalued_skus = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(StoreInventoryValuation.total_cost_minor).filter(
                        StoreInventoryValuation.quantity
                        == InventoryItem.quantity_on_hand
                    ),
                    0,
                ),
                func.count(InventoryItem.id).filter(
                    StoreInventoryValuation.quantity.is_distinct_from(
                        InventoryItem.quantity_on_hand
                    )
                ),
            )
            .select_from(InventoryItem)
            .outerjoin(
                StoreInventoryValuation,
                StoreInventoryValuation.variant_id == InventoryItem.variant_id,
            )
        )
    ).one()

    supplier_unpaid = int(
        await db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StoreSupplierInvoice.total_minor
                        - StoreSupplierInvoice.verified_paid_minor
                    ),
                    0,
                )
            )
        )
        or 0
    )
    approved_credit = int(
        await db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StoreTradeReceivable.amount_minor
                        - StoreTradeReceivable.verified_paid_minor
                    ),
                    0,
                )
            ).where(StoreTradeReceivable.status == "approved")
        )
        or 0
    )
    unvalued_fulfilled_events = int(
        await db.scalar(
            select(func.count(func.distinct(StoreCostEvent.evidence))).where(
                StoreCostEvent.event_kind == "sale_unvalued"
            )
        )
        or 0
    )
    return {
        "order_status_totals": order_status_totals,
        "valued_inventory_minor": (
            int(valued_minor or 0) if int(unvalued_skus or 0) == 0 else None
        ),
        "partial_valued_inventory_minor": int(valued_minor or 0),
        "unvalued_sku_count": int(unvalued_skus or 0),
        "unvalued_fulfilled_orders_count": unvalued_fulfilled_events,
        "supplier_liabilities_minor": supplier_unpaid,
        "approved_credit_exposure_minor": approved_credit,
        "warning": (
            "Inventory valuation reflects verified receipts, approved openings, sales and returns. "
            "Unvalued legacy SKUs remain flagged rather than estimated from retail prices."
        ),
    }
