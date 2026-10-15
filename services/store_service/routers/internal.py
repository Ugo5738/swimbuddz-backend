"""Internal service-to-service endpoints for store-service.

These endpoints are authenticated with service_role JWT only.
They are NOT exposed through the gateway — only other backend services
call them directly via Docker network.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db

router = APIRouter(prefix="/internal/store", tags=["internal"])


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
