"""Transactional Store order lifecycle operations (ADR-024).

Handles:
- Idempotent StoreCredit restoration on cancellation (fixes legacy silent credit loss)
- Wallet hold release / Bubbles refund
- Atomic same-price variant swap with row-locked inventory reservation transfer
- Return inspection restocking with weighted-average COGS reversal
- Unified cancel-and-refund orchestration with payments_service
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    create_internal_refund,
    credit_member_wallet,
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
    release_wallet_hold,
)
from services.store_service.models import (
    AuditEntityType,
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    Order,
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    OrderFulfillmentStatus,
    OrderItem,
    OrderPaymentStatus,
    OrderStatus,
    ProductVariant,
    ReturnInspectionCondition,
    SourcingType,
    StoreCostEvent,
    StoreCredit,
    StoreCreditSourceType,
    StoreCreditTransaction,
    StoreInventoryValuation,
    StoreOrderCase,
    StoreOrderCaseItem,
)
from services.store_service.routers._helpers import log_audit
from services.store_service.services.order_policy import (
    TenderRefundCalculation,
    compute_order_refund_tenders,
    sync_order_status_dimensions,
)
from services.store_service.services.preorder_allocation import (
    allocate_stock_to_paid_preorders,
    explicit_order_reserved_qty,
    is_dropship_line,
    is_preorder_line,
)

logger = get_logger(__name__)


def _to_kobo(amount: Decimal | float | int | None) -> int:
    if amount is None:
        return 0
    return int(round(float(amount) * 100))


async def release_order_inventory_reservations(
    db: AsyncSession, order: Order, performed_by: str
) -> None:
    """Release reserved stock for stocked or allocated preorder items, then reallocate FIFO."""
    items = (
        (
            await db.execute(
                select(OrderItem)
                .where(OrderItem.order_id == order.id)
                .options(
                    selectinload(OrderItem.variant).selectinload(
                        ProductVariant.product
                    ),
                    selectinload(OrderItem.variant).selectinload(
                        ProductVariant.inventory_item
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    for item in items:
        if is_dropship_line(item):
            continue
        inv = (
            await db.execute(
                select(InventoryItem)
                .where(InventoryItem.variant_id == item.variant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not inv:
            continue
        if is_preorder_line(item):
            explicit_reserved = await explicit_order_reserved_qty(db, inv.id, order.id)
            release_qty = min(item.quantity, explicit_reserved, inv.quantity_reserved)
        else:
            release_qty = min(item.quantity, inv.quantity_reserved)
        if release_qty <= 0:
            continue
        inv.quantity_reserved -= release_qty
        db.add(
            InventoryMovement(
                inventory_item_id=inv.id,
                movement_type=InventoryMovementType.RELEASE,
                quantity=-release_qty,
                reference_type="order",
                reference_id=order.id,
                notes=f"Released for cancelled order {order.order_number}",
                performed_by=performed_by,
            )
        )
        await allocate_stock_to_paid_preorders(
            db,
            inv,
            performed_by,
            reason=f"reallocated after cancelled order #{order.order_number}",
            exclude_order_id=order.id,
        )


async def restore_order_store_credits(
    db: AsyncSession,
    order: Order,
    performed_by: str,
    *,
    amount_override_ngn: Optional[Decimal] = None,
    reason: Optional[str] = None,
) -> Optional[StoreCredit]:
    """Idempotently restore Store Credit applied to an order (or issue new return credit)."""
    applied_ngn = Decimal(order.store_credit_applied_ngn or 0)
    target_ngn = amount_override_ngn if amount_override_ngn is not None else applied_ngn
    if target_ngn <= 0 or not order.member_auth_id:
        return None

    # Check if already restored for this order to guarantee idempotency.
    existing_restore = (
        (
            await db.execute(
                select(StoreCreditTransaction).where(
                    StoreCreditTransaction.order_id == order.id,
                    StoreCreditTransaction.transaction_type == "restore",
                )
            )
        )
        .scalars()
        .first()
    )
    if existing_restore is not None and amount_override_ngn is None:
        return await db.get(StoreCredit, existing_restore.store_credit_id)

    # Restore against the original StoreCreditTransaction rows when restoring applied credit.
    if amount_override_ngn is None:
        debit_txs = (
            (
                await db.execute(
                    select(StoreCreditTransaction).where(
                        StoreCreditTransaction.order_id == order.id,
                        StoreCreditTransaction.transaction_type == "debit",
                    )
                )
            )
            .scalars()
            .all()
        )
        if debit_txs:
            first_credit: Optional[StoreCredit] = None
            for tx in debit_txs:
                credit = await db.get(StoreCredit, tx.store_credit_id)
                if credit is not None:
                    credit.balance_ngn = Decimal(credit.balance_ngn) + Decimal(
                        tx.amount_ngn
                    )
                    if first_credit is None:
                        first_credit = credit
                    db.add(
                        StoreCreditTransaction(
                            store_credit_id=credit.id,
                            order_id=order.id,
                            amount_ngn=tx.amount_ngn,
                            transaction_type="restore",
                        )
                    )
            return first_credit

    # Legacy order without StoreCreditTransaction rows, or post-handover Store Credit remedy.
    credit = StoreCredit(
        member_auth_id=order.member_auth_id,
        amount_ngn=target_ngn,
        balance_ngn=target_ngn,
        source_type=StoreCreditSourceType.RETURN,
        source_order_id=order.id,
        reason=reason or f"Store credit refund for order {order.order_number}",
        issued_by=performed_by,
    )
    db.add(credit)
    await db.flush()
    db.add(
        StoreCreditTransaction(
            store_credit_id=credit.id,
            order_id=order.id,
            amount_ngn=target_ngn,
            transaction_type="restore",
        )
    )
    await log_audit(
        db,
        AuditEntityType.STORE_CREDIT,
        credit.id,
        "restored_or_issued",
        performed_by,
        new_value={"amount_ngn": float(target_ngn), "order_id": str(order.id)},
        notes=reason or f"Order {order.order_number}",
    )
    return credit


async def refund_order_bubbles(
    order: Order,
    *,
    bubbles_amount: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    description: Optional[str] = None,
) -> Optional[str]:
    """Release an uncaptured Bubbles hold or refund captured Bubbles idempotently."""
    # Uncaptured hold — release rather than crediting.
    if (
        bubbles_amount is None
        and order.wallet_hold_id
        and not order.wallet_transaction_id
    ):
        try:
            await release_wallet_hold(order.wallet_hold_id, calling_service="store")
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to release wallet hold for order %s", order.order_number
            )
        return None

    target_bubbles = (
        bubbles_amount
        if bubbles_amount is not None
        else int(order.bubbles_applied or 0)
    )
    if target_bubbles <= 0 or not order.member_auth_id:
        return None
    if bubbles_amount is None and not order.wallet_transaction_id:
        return None

    resp = await credit_member_wallet(
        order.member_auth_id,
        amount=target_bubbles,
        idempotency_key=idempotency_key or f"refund-order-{order.id}",
        description=description or f"Refund for order {order.order_number}",
        calling_service="store",
        transaction_type="refund",
        reference_type="order",
        reference_id=str(order.id),
    )
    return resp.get("transaction_id") or resp.get("wallet_transaction_id")


async def swap_order_item_variant(
    db: AsyncSession,
    order: Order,
    order_item: OrderItem,
    new_variant: ProductVariant,
    *,
    performed_by: str,
    customer_notes: Optional[str] = None,
) -> StoreOrderCase:
    """Atomically swap an order item to another same-price active in-stock variant."""
    old_variant = (
        await db.execute(
            select(ProductVariant)
            .where(ProductVariant.id == order_item.variant_id)
            .options(selectinload(ProductVariant.product))
        )
    ).scalar_one_or_none()
    if not old_variant:
        raise HTTPException(
            status_code=404, detail="Original product variant not found"
        )
    if new_variant.id == old_variant.id:
        raise HTTPException(
            status_code=400, detail="Selected variant is already on this order"
        )
    if new_variant.product_id != old_variant.product_id:
        raise HTTPException(
            status_code=422,
            detail="Self-service swap must be for a different size or color of the same product",
        )
    if not new_variant.is_active:
        raise HTTPException(
            status_code=422, detail="Selected variant is currently unavailable"
        )

    new_unit_price = (
        new_variant.price_override_ngn
        if new_variant.price_override_ngn is not None
        else new_variant.product.base_price_ngn
    )
    if Decimal(new_unit_price) != Decimal(order_item.unit_price_ngn):
        raise HTTPException(
            status_code=422,
            detail=(
                "Variant swap is only available at the exact same price "
                f"(current ₦{order_item.unit_price_ngn:,.0f} vs selected ₦{new_unit_price:,.0f}). "
                "Cancel and reorder or submit a cancellation request for a different price."
            ),
        )

    is_stocked = (
        not order_item.is_preorder
        and order_item.sourcing_type == SourcingType.STOCKED.value
    )
    if is_stocked:
        # Lock inventory rows in deterministic UUID order to prevent deadlocks.
        pair = sorted([old_variant.id, new_variant.id], key=str)
        locked_invs = {
            inv.variant_id: inv
            for inv in (
                await db.execute(
                    select(InventoryItem)
                    .where(InventoryItem.variant_id.in_(pair))
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        }
        new_inv = locked_invs.get(new_variant.id)
        old_inv = locked_invs.get(old_variant.id)
        if not new_inv or new_inv.quantity_available < order_item.quantity:
            avail = new_inv.quantity_available if new_inv else 0
            raise HTTPException(
                status_code=409,
                detail=f"Only {avail} unit(s) available for {new_variant.sku}",
            )
        if old_inv:
            release_qty = min(order_item.quantity, old_inv.quantity_reserved)
            if release_qty > 0:
                old_inv.quantity_reserved -= release_qty
                db.add(
                    InventoryMovement(
                        inventory_item_id=old_inv.id,
                        movement_type=InventoryMovementType.RELEASE,
                        quantity=-release_qty,
                        reference_type="order_swap",
                        reference_id=order.id,
                        notes=f"Swapped {old_variant.sku} -> {new_variant.sku} on {order.order_number}",
                        performed_by=performed_by,
                    )
                )
        new_inv.quantity_reserved += order_item.quantity
        db.add(
            InventoryMovement(
                inventory_item_id=new_inv.id,
                movement_type=InventoryMovementType.RESERVATION,
                quantity=order_item.quantity,
                reference_type="order_swap",
                reference_id=order.id,
                notes=f"Swapped {old_variant.sku} -> {new_variant.sku} on {order.order_number}",
                performed_by=performed_by,
            )
        )

    old_snapshot = {
        "variant_id": str(order_item.variant_id),
        "variant_name": order_item.variant_name,
        "sku": order_item.sku,
    }
    order_item.variant_id = new_variant.id
    order_item.variant_name = new_variant.name
    order_item.sku = new_variant.sku

    now = utc_now()
    case = StoreOrderCase(
        case_number=StoreOrderCase.generate_case_number(),
        order_id=order.id,
        member_auth_id=order.member_auth_id,
        case_type=OrderCaseType.VARIANT_SWAP,
        status=OrderCaseStatus.COMPLETED,
        reason_code="variant_swap",
        customer_notes=customer_notes,
        preferred_resolution=OrderCaseResolution.VARIANT_EXCHANGE,
        approved_resolution=OrderCaseResolution.VARIANT_EXCHANGE,
        requested_variant_id=new_variant.id,
        reviewed_by_auth_id=performed_by,
        reviewed_at=now,
        resolved_by_auth_id=performed_by,
        resolved_at=now,
        admin_notes=f"Swapped {old_snapshot['sku']} -> {new_variant.sku}",
    )
    db.add(case)
    await db.flush()
    db.add(
        StoreOrderCaseItem(
            case_id=case.id,
            order_item_id=order_item.id,
            quantity=order_item.quantity,
            replacement_variant_id=new_variant.id,
        )
    )
    await log_audit(
        db,
        AuditEntityType.ORDER,
        order.id,
        "variant_swapped",
        performed_by,
        old_value=old_snapshot,
        new_value={
            "variant_id": str(new_variant.id),
            "variant_name": new_variant.name,
            "sku": new_variant.sku,
            "case_number": case.case_number,
        },
        notes=customer_notes,
    )
    return case


async def restock_returned_case_items(
    db: AsyncSession,
    order: Order,
    case: StoreOrderCase,
    *,
    return_condition: ReturnInspectionCondition,
    performed_by: str,
) -> int:
    """Restock returned items ONLY if condition is RESTOCKABLE and reverse COGS."""
    if case.restocked_at is not None:
        return 0
    if return_condition != ReturnInspectionCondition.RESTOCKABLE:
        return 0

    case_items = (
        (
            await db.execute(
                select(StoreOrderCaseItem)
                .where(StoreOrderCaseItem.case_id == case.id)
                .options(selectinload(StoreOrderCaseItem.order_item))
            )
        )
        .scalars()
        .all()
    )

    # If case covers the full order (no explicit case items), restock all order lines.
    lines_to_restock: list[tuple[OrderItem, int]] = []
    if case_items:
        for ci in case_items:
            if ci.order_item:
                lines_to_restock.append((ci.order_item, ci.quantity))
    else:
        order_items = (
            (await db.execute(select(OrderItem).where(OrderItem.order_id == order.id)))
            .scalars()
            .all()
        )
        lines_to_restock = [(oi, oi.quantity) for oi in order_items]

    restocked_units = 0
    for line, qty in lines_to_restock:
        if qty <= 0:
            continue
        inv = (
            await db.execute(
                select(InventoryItem)
                .where(InventoryItem.variant_id == line.variant_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not inv:
            continue

        inv.quantity_on_hand += qty
        restocked_units += qty
        db.add(
            InventoryMovement(
                inventory_item_id=inv.id,
                movement_type=InventoryMovementType.RETURN,
                quantity=qty,
                reference_type="order_case",
                reference_id=case.id,
                notes=f"Restocked from return case {case.case_number} (order {order.order_number})",
                performed_by=performed_by,
            )
        )
        await allocate_stock_to_paid_preorders(
            db,
            inv,
            performed_by,
            reason=f"restocked return case {case.case_number}",
            exclude_order_id=order.id,
        )

        # Reverse proportional weighted-average cost if a sale cost event exists.
        sale_cost = (
            await db.execute(
                select(StoreCostEvent).where(
                    StoreCostEvent.reference == f"sale:{line.id}",
                    StoreCostEvent.event_kind == "sale",
                )
            )
        ).scalar_one_or_none()
        if sale_cost and sale_cost.quantity_delta < 0 and line.quantity > 0:
            total_sold_cost = abs(int(sale_cost.cost_delta_minor))
            restored_cost_minor = (total_sold_cost * qty) // line.quantity
            valuation = (
                await db.execute(
                    select(StoreInventoryValuation)
                    .where(StoreInventoryValuation.variant_id == line.variant_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if valuation is not None:
                valuation.quantity += qty
                valuation.total_cost_minor += restored_cost_minor
                db.add(
                    StoreCostEvent(
                        variant_id=line.variant_id,
                        event_kind="return_restock",
                        quantity_delta=qty,
                        cost_delta_minor=restored_cost_minor,
                        reference=f"return:{case.id}:{line.id}",
                        evidence=f"return-case:{case.case_number}",
                        actor_auth_id=performed_by,
                    )
                )

    case.restocked_at = utc_now()
    return restocked_units


async def execute_case_financial_resolution(
    db: AsyncSession,
    order: Order,
    case: StoreOrderCase,
    *,
    resolution: OrderCaseResolution,
    include_delivery_fee: bool,
    auto_initiate_paystack: bool,
    actor_auth_id: str,
    reason: str,
    admin_note: Optional[str] = None,
    override_reason: Optional[str] = None,
    target_refund_kobo: Optional[int] = None,
) -> tuple[TenderRefundCalculation, Optional[dict]]:
    """Apply approved resolution (Store Credit, Bubbles, or Cash Refund via payments_service)."""
    calc = compute_order_refund_tenders(
        order,
        include_delivery_fee=include_delivery_fee,
        resolution=resolution,
        target_refund_kobo=target_refund_kobo,
    )

    case.approved_resolution = resolution
    case.approved_refund_cash_ngn = calc.cash_refund_ngn
    case.approved_refund_bubbles = calc.bubbles_refund
    case.approved_refund_store_credit_ngn = calc.store_credit_refund_ngn
    case.delivery_fee_refunded = calc.delivery_fee_included

    wallet_tx_id: Optional[str] = None
    store_credit_id: Optional[str] = None

    # 1. Store Credit restoration / issuance
    if calc.store_credit_refund_kobo > 0:
        if resolution == OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD:
            credit = await restore_order_store_credits(
                db, order, actor_auth_id, reason=reason
            )
        else:
            credit = await restore_order_store_credits(
                db,
                order,
                actor_auth_id,
                amount_override_ngn=calc.store_credit_refund_ngn,
                reason=reason,
            )
        if credit is not None:
            store_credit_id = str(credit.id)

    # 2. Bubbles wallet refund
    if calc.bubbles_refund > 0:
        wallet_tx_id = await refund_order_bubbles(
            order,
            bubbles_amount=calc.bubbles_refund,
            idempotency_key=f"store-case-bubbles:{case.id}",
            description=f"Store refund ({case.case_number}) for order {order.order_number}",
        )

    # 3. Record shared Refund in payments_service (for cash OR audit trail of non-cash)
    refund_data: Optional[dict] = None
    if calc.total_refund_kobo > 0:
        try:
            refund_data = await create_internal_refund(
                {
                    "idempotency_key": f"store-case:{case.id}",
                    "payment_reference": order.payment_reference,
                    "member_auth_id": order.member_auth_id or f"guest:{order.id}",
                    "payer_email": order.customer_email,
                    "source_service": "store",
                    "source_entity_type": "store_order_case",
                    "source_entity_id": str(order.id),
                    "purpose": "store_order",
                    "currency": "NGN",
                    "requested_total_kobo": calc.total_refund_kobo,
                    "cash_refund_kobo": calc.cash_refund_kobo,
                    "bubbles_refund": calc.bubbles_refund,
                    "store_credit_refund_kobo": calc.store_credit_refund_kobo,
                    "reason": reason,
                    "admin_note": admin_note,
                    "override_reason": override_reason,
                    "requested_by_auth_id": case.member_auth_id or actor_auth_id,
                    "approved_by_auth_id": actor_auth_id,
                    "auto_approve": True,
                    "auto_initiate_paystack": auto_initiate_paystack,
                    "recipient_bank_code": case.recipient_bank_code,
                    "recipient_bank_name": case.recipient_bank_name,
                    "recipient_account_number": case.recipient_account_number,
                    "recipient_account_name": case.recipient_account_name,
                    "wallet_refund_transaction_id": wallet_tx_id,
                    "store_credit_id": store_credit_id,
                    "metadata": {
                        "order_number": order.order_number,
                        "case_id": str(case.id),
                        "case_number": case.case_number,
                        "case_type": case.case_type.value,
                    },
                },
                calling_service="store",
            )
        except httpx.HTTPStatusError as exc:
            detail = "Failed to create refund in payments service"
            try:
                detail = exc.response.json().get("detail") or detail
            except ValueError:
                pass
            raise HTTPException(
                status_code=(
                    exc.response.status_code
                    if exc.response.status_code in (400, 404, 409, 422)
                    else status.HTTP_502_BAD_GATEWAY
                ),
                detail=detail,
            ) from exc

        case.refund_reference = refund_data.get("refund_reference")
        case.refund_status = refund_data.get("status")

    now = utc_now()
    if calc.cash_refund_kobo == 0 or (
        refund_data and refund_data.get("status") == "completed"
    ):
        case.status = OrderCaseStatus.COMPLETED
        case.resolved_by_auth_id = actor_auth_id
        case.resolved_at = now
        if calc.total_refund_kobo >= _to_kobo(
            order.effective_order_total_ngn
        ) - _to_kobo(order.delivery_fee_ngn if not include_delivery_fee else 0):
            order.refunded_at = now
            sync_order_status_dimensions(
                order,
                payment_status=OrderPaymentStatus.REFUNDED,
            )
            if order.effective_fulfillment_status in (
                OrderFulfillmentStatus.CANCELLED,
                OrderFulfillmentStatus.RETURNED,
            ):
                order.status = OrderStatus.REFUNDED
    else:
        case.status = OrderCaseStatus.APPROVED

    return calc, refund_data


async def notify_order_case_update(
    order: Order,
    *,
    title: str,
    body: str,
    notif_type: str = "store_order_case_updated",
) -> None:
    """Best-effort member notification for order cancellation/return/refund updates."""
    if not order.member_auth_id:
        return
    try:
        member = await get_member_by_auth_id(
            order.member_auth_id, calling_service="store"
        )
        if member:
            await dispatch_notification(
                type=notif_type,
                category="store",
                member_ids=[str(member["id"])],
                title=title,
                body=body,
                action_url=f"/account/orders/{order.order_number}",
                icon="package",
                metadata={
                    "order_id": str(order.id),
                    "order_number": order.order_number,
                },
                calling_service="store",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to dispatch store case notification for %s: %s",
            order.order_number,
            exc,
        )


async def emit_order_cancelled_or_refunded_event(
    order: Order, *, event_type: str, idempotency_suffix: str
) -> None:
    """Best-effort rewards/analytics event for cancellation or refund."""
    if not order.member_auth_id:
        return
    try:
        await emit_rewards_event(
            event_type=event_type,
            member_auth_id=order.member_auth_id,
            service_source="store",
            event_data={
                "order_number": order.order_number,
                "order_total_ngn": float(order.effective_order_total_ngn),
                "cash_payable_ngn": float(order.total_ngn),
            },
            idempotency_key=f"{event_type}-{order.id}-{idempotency_suffix}",
            calling_service="store",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to emit %s event for order %s: %s",
            event_type,
            order.order_number,
            exc,
        )
