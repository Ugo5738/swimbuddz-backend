"""Customer post-purchase order actions: self-cancel, variant swap, and cases (ADR-023)."""

import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    close_unpaid_store_payment,
    get_payment_reference_status,
    list_internal_refunds_by_source,
)
from libs.db.session import get_async_db
from services.store_service.models import (
    AuditEntityType,
    Order,
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    OrderFulfillmentStatus,
    OrderItem,
    OrderPaymentStatus,
    OrderStatus,
    ProductVariant,
    StoreOrderCase,
    StoreOrderCaseItem,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas.main.cases import (
    OrderActionEligibilityResponse,
    OrderCancelResultResponse,
    OrderCaseCreateRequest,
    OrderCaseItemResponse,
    OrderCaseResponse,
    OrderRefundPreview,
    OrderRefundSummaryItem,
    OrderSelfCancelRequest,
    OrderVariantSwapRequest,
)
from services.store_service.services.order_lifecycle import (
    emit_order_cancelled_or_refunded_event,
    execute_case_financial_resolution,
    notify_order_case_update,
    refund_order_bubbles,
    release_order_inventory_reservations,
    restore_order_store_credits,
    swap_order_item_variant,
)
from services.store_service.services.order_policy import (
    compute_order_refund_tenders,
    get_order_action_eligibility,
    sync_order_status_dimensions,
)

router = APIRouter(tags=["store-order-actions"])
logger = get_logger(__name__)


def _serialize_case(case: StoreOrderCase, order: Optional[Order] = None) -> OrderCaseResponse:
    items_out: list[OrderCaseItemResponse] = []
    for ci in case.items or []:
        oi = ci.order_item
        items_out.append(
            OrderCaseItemResponse(
                id=ci.id,
                order_item_id=ci.order_item_id,
                quantity=ci.quantity,
                replacement_variant_id=ci.replacement_variant_id,
                product_name=oi.product_name if oi else None,
                variant_name=oi.variant_name if oi else None,
                sku=oi.sku if oi else None,
                unit_price_ngn=oi.unit_price_ngn if oi else None,
            )
        )
    target_order = order or getattr(case, "order", None)
    return OrderCaseResponse(
        id=case.id,
        case_number=case.case_number,
        order_id=case.order_id,
        order_number=target_order.order_number if target_order else None,
        customer_name=target_order.customer_name if target_order else None,
        customer_email=target_order.customer_email if target_order else None,
        member_auth_id=case.member_auth_id,
        case_type=case.case_type,
        status=case.status,
        reason_code=case.reason_code,
        customer_notes=case.customer_notes,
        evidence_media_ids=list(case.evidence_media_ids or []),
        preferred_resolution=case.preferred_resolution,
        approved_resolution=case.approved_resolution,
        requested_variant_id=case.requested_variant_id,
        approved_refund_cash_ngn=case.approved_refund_cash_ngn,
        approved_refund_bubbles=case.approved_refund_bubbles,
        approved_refund_store_credit_ngn=case.approved_refund_store_credit_ngn,
        delivery_fee_refunded=case.delivery_fee_refunded,
        refund_reference=case.refund_reference,
        refund_status=case.refund_status,
        recipient_bank_code=case.recipient_bank_code,
        recipient_bank_name=case.recipient_bank_name,
        recipient_account_number=case.recipient_account_number,
        recipient_account_name=case.recipient_account_name,
        return_condition=case.return_condition,
        return_received_at=case.return_received_at,
        restocked_at=case.restocked_at,
        is_policy_override=case.is_policy_override,
        override_reason=case.override_reason,
        admin_notes=case.admin_notes,
        reviewed_by_auth_id=case.reviewed_by_auth_id,
        reviewed_at=case.reviewed_at,
        resolved_by_auth_id=case.resolved_by_auth_id,
        resolved_at=case.resolved_at,
        created_at=case.created_at,
        updated_at=case.updated_at,
        items=items_out,
    )


async def _load_member_order(
    db: AsyncSession,
    order_number: str,
    member_auth_id: str,
    *,
    for_update: bool = False,
) -> Order:
    query = (
        select(Order)
        .where(
            Order.order_number == order_number,
            Order.member_auth_id == member_auth_id,
        )
        .options(
            selectinload(Order.items)
            .selectinload(OrderItem.variant)
            .selectinload(ProductVariant.product),
            selectinload(Order.cases)
            .selectinload(StoreOrderCase.items)
            .selectinload(StoreOrderCaseItem.order_item),
        )
    )
    if for_update:
        query = query.with_for_update(of=Order)
    order = (await db.execute(query)).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/orders/{order_number}/actions", response_model=OrderActionEligibilityResponse)
async def get_order_actions(
    order_number: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return allowed post-purchase actions, refund preview, cases, and live refund status."""
    order = await _load_member_order(db, order_number, current_user.user_id)

    payment_info: Optional[dict] = None
    if order.payment_reference:
        try:
            payment_info = await get_payment_reference_status(
                order.payment_reference, calling_service="store"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not fetch live payment status for order %s: %s",
                order.order_number,
                exc,
            )

    refunds_raw: list[dict] = []
    try:
        refunds_raw = await list_internal_refunds_by_source(
            "store", str(order.id), calling_service="store"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not fetch refunds for order %s: %s", order.order_number, exc
        )

    cases_sorted = sorted(
        order.cases or [], key=lambda c: c.created_at, reverse=True
    )
    elig = get_order_action_eligibility(
        order,
        now=utc_now(),
        active_cases=cases_sorted,
        payment_status_info=payment_info,
    )
    calc = compute_order_refund_tenders(
        order,
        include_delivery_fee=True,
        resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
    )

    return OrderActionEligibilityResponse(
        order_number=order.order_number,
        payment_status=elig.payment_status.value,
        fulfillment_status=elig.fulfillment_status.value,
        can_self_cancel_unpaid=elig.can_self_cancel_unpaid,
        can_self_cancel_paid=elig.can_self_cancel_paid,
        can_request_cancel=elig.can_request_cancel,
        can_swap_variant=elig.can_swap_variant,
        can_request_return_exchange=elig.can_request_return_exchange,
        can_report_defect=elig.can_report_defect,
        has_preorder_or_dropship=elig.has_preorder_or_dropship,
        has_swimwear_items=elig.has_swimwear_items,
        has_open_case=elig.has_open_case,
        payment_pending_review=elig.payment_pending_review,
        return_window_ends_at=elig.return_window_ends_at,
        cancel_block_reason=elig.cancel_block_reason,
        policy_notes=elig.policy_notes,
        full_cancel_refund_preview=OrderRefundPreview(
            cash_refund_ngn=calc.cash_refund_ngn,
            bubbles_refund=calc.bubbles_refund,
            store_credit_refund_ngn=calc.store_credit_refund_ngn,
            total_refund_value_ngn=calc.total_refund_value_ngn,
            delivery_fee_included=calc.delivery_fee_included,
            payment_method=(payment_info or {}).get("payment_method"),
        ),
        cases=[_serialize_case(c, order) for c in cases_sorted],
        refunds=[
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
    )


@router.post("/orders/{order_number}/cancel", response_model=OrderCancelResultResponse)
async def self_cancel_order(
    order_number: str,
    payload: OrderSelfCancelRequest = OrderSelfCancelRequest(),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Self-cancel an unpaid order or a paid stocked order awaiting preparation (ADR-023)."""
    order = await _load_member_order(
        db, order_number, current_user.user_id, for_update=True
    )

    payment_info: Optional[dict] = None
    if order.payment_reference:
        try:
            payment_info = await get_payment_reference_status(
                order.payment_reference, calling_service="store"
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not verify live payment status; please retry shortly.",
            ) from exc

    elig = get_order_action_eligibility(
        order,
        now=utc_now(),
        active_cases=order.cases or [],
        payment_status_info=payment_info,
    )

    if not elig.can_self_cancel_unpaid and not elig.can_self_cancel_paid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=elig.cancel_block_reason
            or "This order cannot be self-cancelled in its current state. Submit a cancellation request instead.",
        )

    now = utc_now()

    # Path A: Unpaid order self-cancellation (with live Paystack lock check)
    if elig.can_self_cancel_unpaid:
        if order.payment_reference:
            try:
                await close_unpaid_store_payment(
                    order.payment_reference, calling_service="store"
                )
            except httpx.HTTPStatusError as exc:
                detail = "Could not close unpaid payment attempt"
                try:
                    detail = exc.response.json().get("detail") or detail
                except ValueError:
                    pass
                raise HTTPException(
                    status_code=(
                        exc.response.status_code
                        if exc.response.status_code in (409, 422, 503)
                        else status.HTTP_502_BAD_GATEWAY
                    ),
                    detail=detail,
                ) from exc

        await release_order_inventory_reservations(db, order, current_user.user_id)
        await restore_order_store_credits(
            db,
            order,
            current_user.user_id,
            reason=f"Restored on customer cancel of unpaid order {order.order_number}",
        )
        await refund_order_bubbles(order)

        order.cancelled_at = now
        sync_order_status_dimensions(
            order,
            legacy_status=OrderStatus.CANCELLED,
            payment_status=OrderPaymentStatus.UNPAID,
            fulfillment_status=OrderFulfillmentStatus.CANCELLED,
        )
        await log_audit(
            db,
            AuditEntityType.ORDER,
            order.id,
            "customer_cancelled_unpaid",
            current_user.user_id,
            new_value={
                "status": order.status.value,
                "reason_code": payload.reason_code,
            },
            notes=payload.customer_notes,
        )
        await db.commit()
        await db.refresh(order)

        await notify_order_case_update(
            order,
            title="Order Cancelled",
            body=f"Your unpaid order #{order.order_number} has been cancelled and any held credits/Bubbles have been restored.",
            notif_type="order_cancelled",
        )
        return OrderCancelResultResponse(
            order_number=order.order_number,
            status=order.status.value,
            payment_status=order.effective_payment_status.value,
            fulfillment_status=order.effective_fulfillment_status.value,
            message="Unpaid order cancelled. Any reserved items, Store Credit, or held Bubbles have been released.",
        )

    # Path B: Paid stocked order awaiting preparation -> cancel + auto-initiate refund
    await release_order_inventory_reservations(db, order, current_user.user_id)
    order.cancelled_at = now
    sync_order_status_dimensions(
        order,
        legacy_status=OrderStatus.CANCELLED,
        payment_status=OrderPaymentStatus.PAID,
        fulfillment_status=OrderFulfillmentStatus.CANCELLED,
    )

    case = StoreOrderCase(
        case_number=StoreOrderCase.generate_case_number(),
        order_id=order.id,
        member_auth_id=current_user.user_id,
        case_type=OrderCaseType.CANCEL_REQUEST,
        status=OrderCaseStatus.APPROVED,
        reason_code=payload.reason_code,
        customer_notes=payload.customer_notes,
        preferred_resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
        approved_resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
        recipient_bank_code=payload.recipient_bank_code,
        recipient_bank_name=payload.recipient_bank_name,
        recipient_account_number=payload.recipient_account_number,
        recipient_account_name=payload.recipient_account_name,
        reviewed_by_auth_id="system:self_cancel",
        reviewed_at=now,
        admin_notes="Auto-approved pre-preparation stocked order self-cancellation",
    )
    db.add(case)
    await db.flush()

    for oi in order.items:
        db.add(
            StoreOrderCaseItem(
                case_id=case.id,
                order_item_id=oi.id,
                quantity=oi.quantity,
            )
        )

    calc, refund_data = await execute_case_financial_resolution(
        db,
        order,
        case,
        resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
        include_delivery_fee=True,
        auto_initiate_paystack=True,
        actor_auth_id=current_user.user_id,
        reason=f"Customer self-cancelled order {order.order_number} ({payload.reason_code})",
        admin_note="Auto-approved pre-preparation self-cancellation",
    )

    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        "customer_self_cancelled_paid",
        current_user.user_id,
        new_value={
            "case_number": case.case_number,
            "refund_reference": case.refund_reference,
            "refund_status": case.refund_status,
            "cash_refund_ngn": float(calc.cash_refund_ngn),
            "bubbles_refund": calc.bubbles_refund,
            "store_credit_refund_ngn": float(calc.store_credit_refund_ngn),
        },
        notes=payload.customer_notes,
    )
    await db.commit()
    await db.refresh(order)
    await db.refresh(case)

    await emit_order_cancelled_or_refunded_event(
        order, event_type="store.order_cancelled", idempotency_suffix=case.case_number
    )
    refund_msg = (
        "Your Paystack refund has been initiated (banks typically credit within 3–10 working days)."
        if refund_data and refund_data.get("provider") == "paystack"
        else (
            "Your bank-transfer refund has been queued for our finance team (target: 2 business days)."
            if calc.cash_refund_kobo > 0
            else "Your Store Credit / Bubbles have been restored immediately."
        )
    )
    await notify_order_case_update(
        order,
        title="Order Cancelled & Refund Started",
        body=f"Order #{order.order_number} has been cancelled. {refund_msg}",
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
        message=f"Order cancelled. {refund_msg}",
    )


@router.post("/orders/{order_number}/swap-variant", response_model=OrderCaseResponse)
async def self_swap_order_variant(
    order_number: str,
    payload: OrderVariantSwapRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Perform an instant same-price variant swap before preparation starts."""
    order = await _load_member_order(
        db, order_number, current_user.user_id, for_update=True
    )
    elig = get_order_action_eligibility(
        order, now=utc_now(), active_cases=order.cases or []
    )
    if not elig.can_swap_variant:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Variant swap is only available before preparation begins.",
        )

    order_item = next(
        (oi for oi in order.items if oi.id == payload.order_item_id), None
    )
    if not order_item:
        raise HTTPException(status_code=404, detail="Order item not found")

    new_variant = (
        await db.execute(
            select(ProductVariant)
            .where(ProductVariant.id == payload.new_variant_id)
            .options(selectinload(ProductVariant.product))
        )
    ).scalar_one_or_none()
    if not new_variant:
        raise HTTPException(status_code=404, detail="Selected variant not found")

    case = await swap_order_item_variant(
        db,
        order,
        order_item,
        new_variant,
        performed_by=current_user.user_id,
        customer_notes=payload.customer_notes,
    )
    await db.commit()
    await db.refresh(case)

    await notify_order_case_update(
        order,
        title="Order Updated",
        body=f"Item on order #{order.order_number} updated to {new_variant.name or new_variant.sku}.",
    )
    return _serialize_case(case, order)


@router.post(
    "/orders/{order_number}/cases",
    response_model=OrderCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order_case(
    order_number: str,
    payload: OrderCaseCreateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Submit a post-purchase request (cancel_request, return_exchange, or defect_claim)."""
    order = await _load_member_order(
        db, order_number, current_user.user_id, for_update=True
    )

    payment_info: Optional[dict] = None
    if order.payment_reference:
        try:
            payment_info = await get_payment_reference_status(
                order.payment_reference, calling_service="store"
            )
        except Exception:  # noqa: BLE001
            payment_info = None

    elig = get_order_action_eligibility(
        order,
        now=utc_now(),
        active_cases=order.cases or [],
        payment_status_info=payment_info,
    )

    if payload.case_type == OrderCaseType.CANCEL_REQUEST:
        if not (elig.can_request_cancel or elig.can_self_cancel_paid):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=elig.cancel_block_reason
                or "Cancellation request is not available for this order.",
            )
    elif payload.case_type == OrderCaseType.RETURN_EXCHANGE:
        if not elig.can_request_return_exchange:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This order is not currently within the 7-day return/exchange window.",
            )
        # Non-defective post-handover returns use exchange, Store Credit, or Bubbles (ADR-023)
        allowed_non_defect = {
            None,
            OrderCaseResolution.VARIANT_EXCHANGE,
            OrderCaseResolution.STORE_CREDIT,
            OrderCaseResolution.WALLET_BUBBLES,
        }
        if payload.preferred_resolution not in allowed_non_defect:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Change-of-mind returns after handover qualify for a size/color exchange, "
                    "Store Credit, or Bubbles. For defective or incorrect items, submit a defect claim."
                ),
            )
    elif payload.case_type == OrderCaseType.DEFECT_CLAIM:
        if not elig.can_report_defect:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Defect or wrong-item claims can only be submitted after handover (and when no open return claim exists).",
            )
    elif payload.case_type == OrderCaseType.VARIANT_SWAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use POST /store/orders/{order_number}/swap-variant for same-price variant swaps.",
        )

    # Validate selected items (or default to all items on the order)
    order_items_by_id = {oi.id: oi for oi in order.items}
    selected_items: list[tuple[OrderItem, int, Optional[uuid.UUID]]] = []
    if payload.items:
        for req_item in payload.items:
            oi = order_items_by_id.get(req_item.order_item_id)
            if not oi:
                raise HTTPException(
                    status_code=400, detail="Selected item does not belong to this order"
                )
            if req_item.quantity > oi.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Requested quantity exceeds ordered quantity for {oi.product_name}",
                )
            selected_items.append((oi, req_item.quantity, req_item.replacement_variant_id))
    else:
        selected_items = [(oi, oi.quantity, None) for oi in order.items]

    preferred = payload.preferred_resolution
    if preferred is None:
        if payload.case_type == OrderCaseType.CANCEL_REQUEST:
            preferred = OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD
        elif payload.case_type == OrderCaseType.RETURN_EXCHANGE:
            preferred = OrderCaseResolution.VARIANT_EXCHANGE
        elif payload.case_type == OrderCaseType.DEFECT_CLAIM:
            preferred = OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD

    case = StoreOrderCase(
        case_number=StoreOrderCase.generate_case_number(),
        order_id=order.id,
        member_auth_id=current_user.user_id,
        case_type=payload.case_type,
        status=OrderCaseStatus.SUBMITTED,
        reason_code=payload.reason_code,
        customer_notes=payload.customer_notes,
        evidence_media_ids=list(payload.evidence_media_ids or []),
        preferred_resolution=preferred,
        requested_variant_id=payload.requested_variant_id,
        recipient_bank_code=payload.recipient_bank_code,
        recipient_bank_name=payload.recipient_bank_name,
        recipient_account_number=payload.recipient_account_number,
        recipient_account_name=payload.recipient_account_name,
    )
    db.add(case)
    await db.flush()

    for oi, qty, repl_variant_id in selected_items:
        db.add(
            StoreOrderCaseItem(
                case_id=case.id,
                order_item_id=oi.id,
                quantity=qty,
                replacement_variant_id=repl_variant_id,
            )
        )

    await log_audit(
        db,
        AuditEntityType.ORDER_CASE,
        case.id,
        "submitted",
        current_user.user_id,
        new_value={
            "case_number": case.case_number,
            "order_id": str(order.id),
            "case_type": case.case_type.value,
            "reason_code": case.reason_code,
        },
        notes=payload.customer_notes,
    )
    await db.commit()

    # Reload with items
    loaded = (
        await db.execute(
            select(StoreOrderCase)
            .where(StoreOrderCase.id == case.id)
            .options(
                selectinload(StoreOrderCase.items).selectinload(
                    StoreOrderCaseItem.order_item
                )
            )
        )
    ).scalar_one()

    await notify_order_case_update(
        order,
        title="Request Received",
        body=f"We received your {case.case_type.value.replace('_', ' ')} request ({case.case_number}) for order #{order.order_number}. We aim to review within 1 business day.",
    )
    return _serialize_case(loaded, order)


@router.get("/orders/{order_number}/cases", response_model=list[OrderCaseResponse])
async def list_my_order_cases(
    order_number: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List post-purchase cases submitted for a member's order."""
    order = await _load_member_order(db, order_number, current_user.user_id)
    cases_sorted = sorted(
        order.cases or [], key=lambda c: c.created_at, reverse=True
    )
    return [_serialize_case(c, order) for c in cases_sorted]


@router.post(
    "/orders/{order_number}/cases/{case_id}/withdraw",
    response_model=OrderCaseResponse,
)
async def withdraw_order_case(
    order_number: str,
    case_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Withdraw a submitted case before admin review."""
    order = await _load_member_order(
        db, order_number, current_user.user_id, for_update=True
    )
    case = next((c for c in (order.cases or []) if c.id == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case.status != OrderCaseStatus.SUBMITTED:
        raise HTTPException(
            status_code=409,
            detail=f"Only submitted cases can be withdrawn (current: {case.status.value})",
        )
    case.status = OrderCaseStatus.CANCELLED
    await log_audit(
        db,
        AuditEntityType.ORDER_CASE,
        case.id,
        "withdrawn",
        current_user.user_id,
        new_value={"status": OrderCaseStatus.CANCELLED.value},
    )
    await db.commit()
    await db.refresh(case)
    return _serialize_case(case, order)
