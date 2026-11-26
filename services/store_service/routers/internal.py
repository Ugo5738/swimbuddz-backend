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


@router.post("/orders/refund-status-callback", include_in_schema=False)
async def handle_refund_status_callback(
    payload: dict,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Receive trusted refund lifecycle updates from payments_service (ADR-023)."""
    from libs.common.datetime_utils import utc_now
    from services.store_service.models import (
        AuditEntityType,
        Order,
        OrderCaseStatus,
        OrderFulfillmentStatus,
        OrderPaymentStatus,
        OrderStatus,
        StoreOrderCase,
    )
    from services.store_service.routers._helpers import log_audit
    from services.store_service.schemas.main.cases import StoreRefundStatusCallback
    from services.store_service.services.order_lifecycle import (
        emit_order_cancelled_or_refunded_event,
        notify_order_case_update,
    )
    from services.store_service.services.order_policy import (
        sync_order_status_dimensions,
    )

    data = StoreRefundStatusCallback.model_validate(payload)
    try:
        order_uuid = uuid.UUID(data.source_entity_id)
    except ValueError:
        return {"updated": False, "reason": "invalid_order_id"}

    order = (
        await db.execute(
            select(Order).where(Order.id == order_uuid).with_for_update()
        )
    ).scalar_one_or_none()
    if not order:
        return {"updated": False, "reason": "order_not_found"}

    case = (
        await db.execute(
            select(StoreOrderCase)
            .where(StoreOrderCase.refund_reference == data.refund_reference)
            .with_for_update()
        )
    ).scalar_one_or_none()

    now = utc_now()
    if case is not None:
        case.refund_status = data.status
        if data.status == "completed":
            case.status = OrderCaseStatus.COMPLETED
            case.resolved_at = case.resolved_at or data.completed_at or now
            case.resolved_by_auth_id = case.resolved_by_auth_id or "payments_service"
        elif data.status == "failed" and data.failure_reason:
            case.admin_notes = (
                f"{case.admin_notes}\n[Refund failed] {data.failure_reason}".strip()
                if case.admin_notes
                else f"[Refund failed] {data.failure_reason}"
            )

    if data.status == "completed":
        cash_paid_kobo = int(round(float(order.total_ngn or 0) * 100))
        is_full = data.cash_refund_kobo >= cash_paid_kobo
        order.refunded_at = order.refunded_at or data.completed_at or now
        sync_order_status_dimensions(
            order,
            payment_status=(
                OrderPaymentStatus.REFUNDED
                if is_full
                else OrderPaymentStatus.PARTIALLY_REFUNDED
            ),
        )
        if is_full and order.effective_fulfillment_status in (
            OrderFulfillmentStatus.CANCELLED,
            OrderFulfillmentStatus.RETURNED,
        ):
            order.status = OrderStatus.REFUNDED

    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        f"refund_{data.status}",
        "payments_service",
        new_value={
            "refund_reference": data.refund_reference,
            "status": data.status,
            "provider_refund_id": data.provider_refund_id,
            "external_bank_reference": data.external_bank_reference,
        },
        notes=data.failure_reason,
    )
    await db.commit()

    if data.status == "completed":
        await emit_order_cancelled_or_refunded_event(
            order,
            event_type="store.order_refunded",
            idempotency_suffix=data.refund_reference,
        )
        await notify_order_case_update(
            order,
            title="Refund Completed",
            body=f"Your refund ({data.refund_reference}) for order #{order.order_number} has been completed.",
            notif_type="order_refunded",
        )
    return {"updated": True, "order_number": order.order_number, "status": data.status}


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
