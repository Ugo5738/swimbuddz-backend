"""Admin post-purchase case review, return inspection, and audited cancel-and-refund (ADR-023)."""

import uuid
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import list_internal_refunds_by_source
from libs.db.session import get_async_db
from services.store_service.models import (
    AuditEntityType,
    Order,
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    OrderFulfillmentStatus,
    OrderPaymentStatus,
    OrderStatus,
    ReturnInspectionCondition,
    StoreOrderCase,
    StoreOrderCaseItem,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.routers.order_actions import _serialize_case
from services.store_service.schemas.main.cases import (
    AdminCancelAndRefundRequest,
    AdminCaseReviewRequest,
    AdminReceiveReturnRequest,
    OrderCancelResultResponse,
    OrderCaseListResponse,
    OrderCaseResponse,
    OrderRefundSummaryItem,
)
from services.store_service.services.order_lifecycle import (
    emit_order_cancelled_or_refunded_event,
    execute_case_financial_resolution,
    notify_order_case_update,
    refund_order_bubbles,
    release_order_inventory_reservations,
    restock_returned_case_items,
    restore_order_store_credits,
)
from services.store_service.services.order_policy import (
    OPEN_CASE_STATUSES,
    sync_order_status_dimensions,
)

router = APIRouter(tags=["admin-store-cases"])
logger = get_logger(__name__)


def _case_eager_options():
    return (
        selectinload(StoreOrderCase.order).selectinload(Order.items),
        selectinload(StoreOrderCase.items).selectinload(StoreOrderCaseItem.order_item),
    )


def _compute_case_items_target_kobo(order: Order, case: StoreOrderCase) -> Optional[int]:
    """Compute partial-case item value when a return covers a subset of items."""
    case_items = list(case.items or [])
    order_items = list(order.items or [])
    if not case_items or not order_items:
        return None
    full_qty = sum(oi.quantity for oi in order_items)
    case_qty = sum(ci.quantity for ci in case_items)
    if case_qty >= full_qty:
        return None  # Full order

    total_kobo = 0
    for ci in case_items:
        oi = ci.order_item
        if not oi or oi.quantity <= 0:
            continue
        net_line = (
            Decimal(oi.net_line_total_ngn)
            if oi.net_line_total_ngn is not None
            else Decimal(oi.line_total_ngn)
        )
        line_kobo = int(round(float(net_line) * 100))
        total_kobo += (line_kobo * ci.quantity) // oi.quantity
    return total_kobo


@router.get("/cases/open-count")
async def get_open_cases_count(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Count open store post-purchase cases needing admin action."""
    count = (
        await db.execute(
            select(func.count(StoreOrderCase.id)).where(
                StoreOrderCase.status.in_(tuple(OPEN_CASE_STATUSES))
            )
        )
    ).scalar_one() or 0
    return {"open_count": int(count)}


@router.get("/cases", response_model=OrderCaseListResponse)
async def list_store_cases_admin(
    status_filter: Optional[OrderCaseStatus] = Query(default=None, alias="status"),
    case_type: Optional[OrderCaseType] = None,
    order_id: Optional[uuid.UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List post-purchase order cases for Store operations review."""
    query = select(StoreOrderCase)
    if status_filter is not None:
        query = query.where(StoreOrderCase.status == status_filter)
    if case_type is not None:
        query = query.where(StoreOrderCase.case_type == case_type)
    if order_id is not None:
        query = query.where(StoreOrderCase.order_id == order_id)

    total = (
        await db.execute(select(func.count()).select_from(query.subquery()))
    ).scalar() or 0

    rows = (
        (
            await db.execute(
                query.options(*_case_eager_options())
                .order_by(StoreOrderCase.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return OrderCaseListResponse(
        items=[_serialize_case(c) for c in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/cases/{case_id}", response_model=OrderCaseResponse)
async def get_store_case_admin(
    case_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Fetch a single StoreOrderCase with order & item details."""
    case = (
        await db.execute(
            select(StoreOrderCase)
            .where(StoreOrderCase.id == case_id)
            .options(*_case_eager_options())
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return _serialize_case(case)


@router.get("/orders/{order_id}/cases")
async def list_order_cases_and_refunds_admin(
    order_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Fetch all post-purchase cases and live payments_service refunds for an admin order."""
    order = (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.cases)
                .selectinload(StoreOrderCase.items)
                .selectinload(StoreOrderCaseItem.order_item)
            )
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    refunds_raw: list[dict] = []
    try:
        refunds_raw = await list_internal_refunds_by_source(
            "store", str(order.id), calling_service="store"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not load payments_service refunds for admin order %s: %s",
            order.order_number,
            exc,
        )

    cases_sorted = sorted(
        order.cases or [], key=lambda c: c.created_at, reverse=True
    )
    return {
        "cases": [_serialize_case(c, order) for c in cases_sorted],
        "refunds": [
            OrderRefundSummaryItem(
                refund_reference=r["refund_reference"],
                status=r["status"],
                refund_method=r["refund_method"],
                provider=r.get("provider"),
                cash_refund_kobo=int(r.get("cash_refund_kobo") or 0),
                bubbles_refund=int(r.get("bubbles_refund") or 0),
                store_credit_refund_kobo=int(r.get("store_credit_refund_kobo") or 0),
                provider_refund_id=r.get("provider_refund_id"),
                external_bank_reference=r.get("external_bank_reference"),
                failure_reason=r.get("failure_reason"),
                created_at=r.get("created_at"),
                completed_at=r.get("completed_at"),
            )
            for r in refunds_raw
        ],
    }


@router.post("/cases/{case_id}/review", response_model=OrderCaseResponse)
async def review_store_case_admin(
    case_id: uuid.UUID,
    payload: AdminCaseReviewRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve or decline a customer post-purchase case (ADR-023)."""
    case = (
        await db.execute(
            select(StoreOrderCase)
            .where(StoreOrderCase.id == case_id)
            .options(*_case_eager_options())
            .with_for_update(of=StoreOrderCase)
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.status not in (OrderCaseStatus.SUBMITTED, OrderCaseStatus.UNDER_REVIEW):
        raise HTTPException(
            status_code=409,
            detail=f"Case cannot be reviewed in status '{case.status.value}'",
        )
    if payload.is_policy_override and not (payload.override_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="override_reason is required whenever is_policy_override is true",
        )

    order = case.order
    now = utc_now()
    case.reviewed_by_auth_id = current_user.user_id
    case.reviewed_at = now
    case.is_policy_override = payload.is_policy_override
    case.override_reason = payload.override_reason
    if payload.admin_notes:
        case.admin_notes = payload.admin_notes

    if payload.decision == "decline":
        if not (payload.admin_notes or "").strip():
            raise HTTPException(
                status_code=422,
                detail="Please provide an explanation in admin_notes when declining a case.",
            )
        case.status = OrderCaseStatus.DECLINED
        case.resolved_by_auth_id = current_user.user_id
        case.resolved_at = now
        await log_audit(
            db,
            AuditEntityType.ORDER_CASE,
            case.id,
            "declined",
            current_user.user_id,
            new_value={"status": case.status.value},
            notes=payload.admin_notes,
        )
        await db.commit()
        await db.refresh(case)
        await notify_order_case_update(
            order,
            title=f"Update on Request {case.case_number}",
            body=f"Your request for order #{order.order_number} could not be approved: {payload.admin_notes}",
        )
        return _serialize_case(case, order)

    # Approved!
    resolution = (
        payload.approved_resolution
        or case.preferred_resolution
        or (
            OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD
            if case.case_type in (OrderCaseType.CANCEL_REQUEST, OrderCaseType.DEFECT_CLAIM)
            else OrderCaseResolution.STORE_CREDIT
        )
    )
    case.approved_resolution = resolution

    # Enforce ADR-023 change-of-mind policy unless explicit policy override is recorded.
    if (
        case.case_type == OrderCaseType.RETURN_EXCHANGE
        and resolution
        in (
            OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
            OrderCaseResolution.PARTIAL_REFUND_ORIGINAL_METHOD,
        )
        and not payload.is_policy_override
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "Change-of-mind returns after handover use Exchange, Store Credit, or Bubbles. "
                "Enable Policy Override with an explicit override_reason to issue a cash refund."
            ),
        )

    # Path 1: Approved cancellation request -> cancel order & refund
    if case.case_type == OrderCaseType.CANCEL_REQUEST:
        was_paid = order.effective_payment_status in (
            OrderPaymentStatus.PAID,
            OrderPaymentStatus.PARTIALLY_REFUNDED,
        )
        await release_order_inventory_reservations(db, order, current_user.user_id)
        order.cancelled_at = now
        sync_order_status_dimensions(
            order,
            legacy_status=OrderStatus.CANCELLED,
            payment_status=(
                OrderPaymentStatus.PAID if was_paid else OrderPaymentStatus.UNPAID
            ),
            fulfillment_status=OrderFulfillmentStatus.CANCELLED,
        )
        if was_paid:
            await execute_case_financial_resolution(
                db,
                order,
                case,
                resolution=resolution,
                include_delivery_fee=True,
                auto_initiate_paystack=payload.auto_initiate_paystack,
                actor_auth_id=current_user.user_id,
                reason=f"Approved cancellation request {case.case_number} for order {order.order_number}",
                admin_note=payload.admin_notes,
                override_reason=payload.override_reason,
            )
        else:
            await restore_order_store_credits(
                db, order, current_user.user_id, reason=f"Approved {case.case_number}"
            )
            await refund_order_bubbles(order)
            case.status = OrderCaseStatus.COMPLETED
            case.resolved_by_auth_id = current_user.user_id
            case.resolved_at = now

        await log_audit(
            db,
            AuditEntityType.ORDER_CASE,
            case.id,
            "approved_cancel",
            current_user.user_id,
            new_value={
                "status": case.status.value,
                "resolution": resolution.value,
                "refund_reference": case.refund_reference,
            },
            notes=payload.admin_notes,
        )
        await db.commit()
        await db.refresh(case)
        await emit_order_cancelled_or_refunded_event(
            order, event_type="store.order_cancelled", idempotency_suffix=case.case_number
        )
        await notify_order_case_update(
            order,
            title=f"Cancellation Approved ({case.case_number})",
            body=f"Your cancellation request for order #{order.order_number} was approved.",
        )
        return _serialize_case(case, order)

    # Path 2: Return / Exchange or Defect Claim
    if payload.require_return_before_resolution:
        case.status = OrderCaseStatus.AWAITING_RETURN
        await log_audit(
            db,
            AuditEntityType.ORDER_CASE,
            case.id,
            "approved_awaiting_return",
            current_user.user_id,
            new_value={
                "status": case.status.value,
                "approved_resolution": resolution.value,
            },
            notes=payload.admin_notes,
        )
        await db.commit()
        await db.refresh(case)
        await notify_order_case_update(
            order,
            title=f"Return Approved ({case.case_number})",
            body=f"Your request for order #{order.order_number} is approved. Please return the item(s) at your next SwimBuddz pool session or via dispatch so we can complete your resolution.",
        )
        return _serialize_case(case, order)

    # Immediate resolution without waiting for physical return (e.g., clear defect photo)
    include_delivery = (
        True if case.case_type == OrderCaseType.DEFECT_CLAIM else payload.include_delivery_fee
    )
    target_kobo = _compute_case_items_target_kobo(order, case)
    await execute_case_financial_resolution(
        db,
        order,
        case,
        resolution=resolution,
        include_delivery_fee=include_delivery,
        auto_initiate_paystack=payload.auto_initiate_paystack,
        actor_auth_id=current_user.user_id,
        reason=f"Approved {case.case_type.value} ({case.case_number}) for order {order.order_number}",
        admin_note=payload.admin_notes,
        override_reason=payload.override_reason,
        target_refund_kobo=target_kobo,
    )
    await log_audit(
        db,
        AuditEntityType.ORDER_CASE,
        case.id,
        "approved_resolved",
        current_user.user_id,
        new_value={
            "status": case.status.value,
            "resolution": resolution.value,
            "refund_reference": case.refund_reference,
        },
        notes=payload.admin_notes,
    )
    await db.commit()
    await db.refresh(case)
    await notify_order_case_update(
        order,
        title=f"Request Approved ({case.case_number})",
        body=f"Your request for order #{order.order_number} has been approved ({resolution.value.replace('_', ' ')}).",
    )
    return _serialize_case(case, order)


@router.post("/cases/{case_id}/receive-return", response_model=OrderCaseResponse)
async def receive_return_admin(
    case_id: uuid.UUID,
    payload: AdminReceiveReturnRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Record receipt & physical inspection of returned items, restock if eligible, and execute remedy."""
    case = (
        await db.execute(
            select(StoreOrderCase)
            .where(StoreOrderCase.id == case_id)
            .options(*_case_eager_options())
            .with_for_update(of=StoreOrderCase)
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.status not in (
        OrderCaseStatus.AWAITING_RETURN,
        OrderCaseStatus.APPROVED,
        OrderCaseStatus.RETURN_RECEIVED,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Case is not awaiting return (status: {case.status.value})",
        )
    if payload.return_condition == ReturnInspectionCondition.PENDING:
        raise HTTPException(
            status_code=422,
            detail="Select an inspection condition: restockable, damaged, or hygiene_unsellable.",
        )

    order = case.order
    now = utc_now()
    case.return_condition = payload.return_condition
    case.return_received_at = case.return_received_at or now
    if payload.admin_notes:
        case.admin_notes = payload.admin_notes

    restocked_units = await restock_returned_case_items(
        db,
        order,
        case,
        return_condition=payload.return_condition,
        performed_by=current_user.user_id,
    )

    sync_order_status_dimensions(
        order,
        fulfillment_status=OrderFulfillmentStatus.RETURNED,
    )

    resolution = (
        payload.override_resolution
        or case.approved_resolution
        or case.preferred_resolution
        or OrderCaseResolution.STORE_CREDIT
    )
    include_delivery = (
        True if case.case_type == OrderCaseType.DEFECT_CLAIM else payload.include_delivery_fee
    )
    target_kobo = _compute_case_items_target_kobo(order, case)

    await execute_case_financial_resolution(
        db,
        order,
        case,
        resolution=resolution,
        include_delivery_fee=include_delivery,
        auto_initiate_paystack=payload.auto_initiate_paystack,
        actor_auth_id=current_user.user_id,
        reason=f"Return received ({payload.return_condition.value}) for {case.case_number}",
        admin_note=payload.admin_notes,
        target_refund_kobo=target_kobo,
    )

    await log_audit(
        db,
        AuditEntityType.ORDER_CASE,
        case.id,
        "return_received",
        current_user.user_id,
        new_value={
            "return_condition": payload.return_condition.value,
            "restocked_units": restocked_units,
            "resolution": resolution.value,
            "refund_reference": case.refund_reference,
            "status": case.status.value,
        },
        notes=payload.admin_notes,
    )
    await db.commit()
    await db.refresh(case)
    await notify_order_case_update(
        order,
        title=f"Return Processed ({case.case_number})",
        body=f"We received your return for order #{order.order_number} and processed your {resolution.value.replace('_', ' ')}.",
    )
    return _serialize_case(case, order)


@router.post(
    "/orders/{order_id}/cancel-and-refund",
    response_model=OrderCancelResultResponse,
)
async def admin_cancel_and_refund_order(
    order_id: uuid.UUID,
    payload: AdminCancelAndRefundRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Audited admin cancellation for paid orders that guarantees refund & tender restoration."""
    order = (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
            .with_for_update(of=Order)
        )
    ).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED):
        raise HTTPException(
            status_code=409,
            detail=f"Order is already {order.status.value}",
        )
    if payload.is_policy_override and not (payload.override_reason or "").strip():
        raise HTTPException(
            status_code=422,
            detail="override_reason is required when is_policy_override is true",
        )

    now = utc_now()
    was_paid = order.effective_payment_status in (
        OrderPaymentStatus.PAID,
        OrderPaymentStatus.PARTIALLY_REFUNDED,
    )
    was_fulfilled = order.effective_fulfillment_status in (
        OrderFulfillmentStatus.PICKED_UP,
        OrderFulfillmentStatus.DELIVERED,
    )

    if not was_fulfilled:
        await release_order_inventory_reservations(db, order, current_user.user_id)

    order.cancelled_at = now
    order.admin_notes = (
        f"{order.admin_notes}\n[Cancel & Refund] {payload.reason}".strip()
        if order.admin_notes
        else f"[Cancel & Refund] {payload.reason}"
    )
    sync_order_status_dimensions(
        order,
        legacy_status=OrderStatus.CANCELLED,
        payment_status=(
            OrderPaymentStatus.PAID if was_paid else OrderPaymentStatus.UNPAID
        ),
        fulfillment_status=(
            OrderFulfillmentStatus.RETURNED
            if was_fulfilled
            else OrderFulfillmentStatus.CANCELLED
        ),
    )

    case = StoreOrderCase(
        case_number=StoreOrderCase.generate_case_number(),
        order_id=order.id,
        member_auth_id=order.member_auth_id,
        case_type=OrderCaseType.CANCEL_REQUEST,
        status=OrderCaseStatus.APPROVED,
        reason_code="admin_cancelled",
        customer_notes=payload.reason,
        preferred_resolution=payload.resolution,
        approved_resolution=payload.resolution,
        is_policy_override=payload.is_policy_override,
        override_reason=payload.override_reason,
        admin_notes=payload.reason,
        reviewed_by_auth_id=current_user.user_id,
        reviewed_at=now,
    )
    db.add(case)
    await db.flush()
    for oi in order.items:
        db.add(
            StoreOrderCaseItem(
                case_id=case.id, order_item_id=oi.id, quantity=oi.quantity
            )
        )

    if was_paid:
        await execute_case_financial_resolution(
            db,
            order,
            case,
            resolution=payload.resolution,
            include_delivery_fee=payload.include_delivery_fee,
            auto_initiate_paystack=payload.auto_initiate_paystack,
            actor_auth_id=current_user.user_id,
            reason=f"Admin cancelled order {order.order_number}: {payload.reason}",
            admin_note=payload.reason,
            override_reason=payload.override_reason,
        )
    else:
        await restore_order_store_credits(
            db, order, current_user.user_id, reason=payload.reason
        )
        await refund_order_bubbles(order)
        case.status = OrderCaseStatus.COMPLETED
        case.resolved_by_auth_id = current_user.user_id
        case.resolved_at = now

    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        "admin_cancel_and_refund",
        current_user.user_id,
        new_value={
            "case_number": case.case_number,
            "resolution": payload.resolution.value,
            "refund_reference": case.refund_reference,
            "refund_status": case.refund_status,
        },
        notes=payload.reason,
    )
    await db.commit()
    await db.refresh(order)
    await db.refresh(case)

    await emit_order_cancelled_or_refunded_event(
        order, event_type="store.order_cancelled", idempotency_suffix=case.case_number
    )
    await notify_order_case_update(
        order,
        title="Order Cancelled & Refund Processed",
        body=f"Order #{order.order_number} has been cancelled by SwimBuddz. Case {case.case_number} tracks your refund.",
        notif_type="order_cancelled",
    )

    return OrderCancelResultResponse(
        order_number=order.order_number,
        status=order.status.value,
        payment_status=order.effective_payment_status.value,
        fulfillment_status=order.effective_fulfillment_status.value,
        case=_serialize_case(case, order),
        refund_reference=case.refund_reference,
        refund_status=case.refund_status,
        message="Order cancelled and refund workflow created.",
    )
