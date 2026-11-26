"""Pure Store post-purchase policy engine (ADR-024).

Deterministic, DB-free rules for:
- Separating payment_status and fulfillment_status while keeping legacy status synced
- Computing available customer actions by fulfillment state, sourcing type, and time window
- Computing multi-tender refund splits (cash + Bubbles + Store Credit) without over-refunding discounts
- Allocating order-level discounts proportionally across order lines
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional

from libs.common.currency import KOBO_PER_BUBBLE
from services.store_service.models.enums import (
    OrderCaseResolution,
    OrderCaseStatus,
    OrderCaseType,
    OrderFulfillmentStatus,
    OrderPaymentStatus,
    OrderStatus,
    SourcingType,
)

RETURN_WINDOW_DAYS = 7
DEFECT_CLAIM_WINDOW_DAYS = 30

OPEN_CASE_STATUSES = {
    OrderCaseStatus.SUBMITTED,
    OrderCaseStatus.UNDER_REVIEW,
    OrderCaseStatus.APPROVED,
    OrderCaseStatus.AWAITING_RETURN,
    OrderCaseStatus.RETURN_RECEIVED,
}

NON_STOCKED_SOURCING_TYPES = {
    SourcingType.PREORDER.value,
    SourcingType.DROPSHIP.value,
    SourcingType.CONSIGNMENT.value,
}


def _to_kobo(amount: Decimal | float | int | None) -> int:
    if amount is None:
        return 0
    dec = Decimal(str(amount))
    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _from_kobo(kobo: int) -> Decimal:
    return (Decimal(kobo) / Decimal("100")).quantize(Decimal("0.01"))


def _ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def allocate_line_net_totals(
    line_subtotals_ngn: list[Decimal],
    discount_amount_ngn: Decimal,
) -> list[Decimal]:
    """Allocate order-level discount across lines in exact kobo (remainder on last line)."""
    if not line_subtotals_ngn:
        return []
    subtotals_kobo = [_to_kobo(v) for v in line_subtotals_ngn]
    gross_kobo = sum(subtotals_kobo)
    discount_kobo = min(_to_kobo(discount_amount_ngn), gross_kobo)
    if gross_kobo <= 0 or discount_kobo <= 0:
        return [_from_kobo(k) for k in subtotals_kobo]

    allocated_discount = 0
    net_lines: list[Decimal] = []
    for idx, line_kobo in enumerate(subtotals_kobo):
        if idx == len(subtotals_kobo) - 1:
            line_disc = discount_kobo - allocated_discount
        else:
            line_disc = (discount_kobo * line_kobo) // gross_kobo
            allocated_discount += line_disc
        net_lines.append(_from_kobo(max(0, line_kobo - line_disc)))
    return net_lines


def sync_order_status_dimensions(
    order,
    *,
    legacy_status: Optional[OrderStatus] = None,
    payment_status: Optional[OrderPaymentStatus] = None,
    fulfillment_status: Optional[OrderFulfillmentStatus] = None,
) -> None:
    """Keep legacy Order.status, payment_status, and fulfillment_status consistent."""
    if legacy_status is not None:
        order.status = legacy_status
        if payment_status is None:
            if legacy_status == OrderStatus.PENDING_PAYMENT:
                order.payment_status = OrderPaymentStatus.UNPAID
            elif legacy_status == OrderStatus.PAYMENT_FAILED:
                order.payment_status = OrderPaymentStatus.FAILED
            elif legacy_status == OrderStatus.REFUNDED:
                order.payment_status = OrderPaymentStatus.REFUNDED
            elif legacy_status == OrderStatus.CANCELLED:
                if order.payment_status is None:
                    order.payment_status = (
                        OrderPaymentStatus.PAID
                        if getattr(order, "paid_at", None)
                        else OrderPaymentStatus.UNPAID
                    )
            else:
                order.payment_status = OrderPaymentStatus.PAID

        if fulfillment_status is None:
            fulfill_map = {
                OrderStatus.PENDING_PAYMENT: OrderFulfillmentStatus.NOT_STARTED,
                OrderStatus.PAYMENT_FAILED: OrderFulfillmentStatus.NOT_STARTED,
                OrderStatus.PAID: OrderFulfillmentStatus.AWAITING_PREPARATION,
                OrderStatus.PROCESSING: OrderFulfillmentStatus.PROCESSING,
                OrderStatus.READY_FOR_PICKUP: OrderFulfillmentStatus.READY_FOR_PICKUP,
                OrderStatus.PICKED_UP: OrderFulfillmentStatus.PICKED_UP,
                OrderStatus.SHIPPED: OrderFulfillmentStatus.SHIPPED,
                OrderStatus.DELIVERED: OrderFulfillmentStatus.DELIVERED,
                OrderStatus.CANCELLED: OrderFulfillmentStatus.CANCELLED,
                OrderStatus.REFUNDED: (
                    OrderFulfillmentStatus.RETURNED
                    if getattr(order, "fulfilled_at", None)
                    else OrderFulfillmentStatus.CANCELLED
                ),
            }
            if legacy_status in fulfill_map:
                order.fulfillment_status = fulfill_map[legacy_status]

    if payment_status is not None:
        order.payment_status = payment_status
    if fulfillment_status is not None:
        order.fulfillment_status = fulfillment_status


@dataclass
class TenderRefundCalculation:
    """Exact multi-tender refund breakdown."""

    cash_refund_kobo: int
    bubbles_refund: int
    store_credit_refund_kobo: int
    total_refund_kobo: int
    delivery_fee_included: bool

    @property
    def cash_refund_ngn(self) -> Decimal:
        return _from_kobo(self.cash_refund_kobo)

    @property
    def store_credit_refund_ngn(self) -> Decimal:
        return _from_kobo(self.store_credit_refund_kobo)

    @property
    def total_refund_value_ngn(self) -> Decimal:
        return _from_kobo(self.total_refund_kobo)


def compute_order_refund_tenders(
    order,
    *,
    include_delivery_fee: bool = True,
    resolution: OrderCaseResolution = OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
    target_refund_kobo: Optional[int] = None,
) -> TenderRefundCalculation:
    """Compute exact refund split across cash, Bubbles, and Store Credit (ADR-024).

    Invariants:
    - Promotional discounts are never refunded as cash.
    - Total refund never exceeds what the customer actually paid across all tenders.
    - Cash refund never exceeds ``order.total_ngn`` (cash actually payable/paid).
    - Non-defective change-of-mind returns convert to Store Credit or Bubbles (zero cash).
    """
    cash_paid_kobo = _to_kobo(getattr(order, "total_ngn", 0))
    bubbles_paid = int(getattr(order, "bubbles_applied", 0) or 0)
    bubbles_paid_kobo = bubbles_paid * KOBO_PER_BUBBLE
    credit_paid_kobo = _to_kobo(getattr(order, "store_credit_applied_ngn", 0))
    delivery_kobo = _to_kobo(getattr(order, "delivery_fee_ngn", 0))

    total_paid_kobo = cash_paid_kobo + bubbles_paid_kobo + credit_paid_kobo
    if total_paid_kobo <= 0:
        return TenderRefundCalculation(0, 0, 0, 0, include_delivery_fee)

    max_refundable_kobo = total_paid_kobo
    if not include_delivery_fee and delivery_kobo > 0:
        max_refundable_kobo = max(0, total_paid_kobo - delivery_kobo)

    desired_kobo = (
        min(max(0, target_refund_kobo), max_refundable_kobo)
        if target_refund_kobo is not None
        else max_refundable_kobo
    )

    if resolution in (
        OrderCaseResolution.NO_REFUND,
        OrderCaseResolution.VARIANT_EXCHANGE,
        OrderCaseResolution.REPLACEMENT_SHIPMENT,
    ):
        return TenderRefundCalculation(0, 0, 0, 0, False)

    if resolution == OrderCaseResolution.STORE_CREDIT:
        return TenderRefundCalculation(
            cash_refund_kobo=0,
            bubbles_refund=0,
            store_credit_refund_kobo=desired_kobo,
            total_refund_kobo=desired_kobo,
            delivery_fee_included=include_delivery_fee,
        )

    if resolution == OrderCaseResolution.WALLET_BUBBLES:
        # Exact 1 Bubble = ₦100; any sub-₦100 remainder stays as Store Credit
        # rather than silently rounding up or dropping customer value (ADR-007).
        whole_bubbles = desired_kobo // KOBO_PER_BUBBLE
        remainder_kobo = desired_kobo % KOBO_PER_BUBBLE
        return TenderRefundCalculation(
            cash_refund_kobo=0,
            bubbles_refund=whole_bubbles,
            store_credit_refund_kobo=remainder_kobo,
            total_refund_kobo=whole_bubbles * KOBO_PER_BUBBLE + remainder_kobo,
            delivery_fee_included=include_delivery_fee,
        )

    # FULL_REFUND_ORIGINAL_METHOD / PARTIAL_REFUND_ORIGINAL_METHOD:
    # Full refund returns each tender 1-to-1; partial refund returns cash first
    # up to cash_paid, then whole Bubbles up to bubbles_paid, then Store Credit.
    if desired_kobo == total_paid_kobo:
        return TenderRefundCalculation(
            cash_refund_kobo=cash_paid_kobo,
            bubbles_refund=bubbles_paid,
            store_credit_refund_kobo=credit_paid_kobo,
            total_refund_kobo=total_paid_kobo,
            delivery_fee_included=include_delivery_fee,
        )

    remaining = desired_kobo
    cash_out = min(cash_paid_kobo, remaining)
    remaining -= cash_out

    bubbles_out = min(bubbles_paid, remaining // KOBO_PER_BUBBLE)
    remaining -= bubbles_out * KOBO_PER_BUBBLE

    credit_out = min(credit_paid_kobo + remaining, remaining)
    # Cap store_credit to desired remainder so total never exceeds desired_kobo
    credit_out = min(credit_out, remaining)

    return TenderRefundCalculation(
        cash_refund_kobo=cash_out,
        bubbles_refund=bubbles_out,
        store_credit_refund_kobo=credit_out,
        total_refund_kobo=cash_out + bubbles_out * KOBO_PER_BUBBLE + credit_out,
        delivery_fee_included=include_delivery_fee,
    )


@dataclass
class OrderActionEligibility:
    """Computed customer actions and policy notes for an order."""

    payment_status: OrderPaymentStatus
    fulfillment_status: OrderFulfillmentStatus
    can_self_cancel_unpaid: bool = False
    can_self_cancel_paid: bool = False
    can_request_cancel: bool = False
    can_swap_variant: bool = False
    can_request_return_exchange: bool = False
    can_report_defect: bool = False
    has_preorder_or_dropship: bool = False
    has_swimwear_items: bool = False
    has_open_case: bool = False
    payment_pending_review: bool = False
    return_window_ends_at: Optional[datetime] = None
    cancel_block_reason: Optional[str] = None
    policy_notes: list[str] = field(default_factory=list)


def get_order_action_eligibility(
    order,
    *,
    now: datetime,
    active_cases: Iterable = (),
    payment_status_info: Optional[dict] = None,
) -> OrderActionEligibility:
    """Evaluate all post-purchase customer actions allowed for this order."""
    now_aware = _ensure_aware(now) or datetime.now(timezone.utc)
    payment_status = order.effective_payment_status
    fulfillment_status = order.effective_fulfillment_status

    payment_pending_review = bool(
        (payment_status_info or {}).get("has_proof")
        or (payment_status_info or {}).get("status") == "pending_review"
        or payment_status == OrderPaymentStatus.PENDING_REVIEW
    )
    if payment_pending_review and payment_status == OrderPaymentStatus.UNPAID:
        payment_status = OrderPaymentStatus.PENDING_REVIEW

    items = list(getattr(order, "items", None) or [])
    has_preorder_or_dropship = any(
        bool(getattr(it, "is_preorder", False))
        or str(getattr(it, "sourcing_type", "stocked")).lower()
        in NON_STOCKED_SOURCING_TYPES
        for it in items
    )
    has_swimwear_items = any(
        bool(getattr(it, "requires_size_chart_ack", False))
        or bool(
            getattr(
                getattr(getattr(it, "variant", None), "product", None),
                "requires_size_chart_ack",
                False,
            )
        )
        for it in items
    )

    open_cases = [c for c in active_cases if c.status in OPEN_CASE_STATUSES]
    has_open_case = len(open_cases) > 0
    has_open_cancel = any(
        c.case_type == OrderCaseType.CANCEL_REQUEST for c in open_cases
    )
    has_open_return_or_defect = any(
        c.case_type in (OrderCaseType.RETURN_EXCHANGE, OrderCaseType.DEFECT_CLAIM)
        for c in open_cases
    )

    elig = OrderActionEligibility(
        payment_status=payment_status,
        fulfillment_status=fulfillment_status,
        has_preorder_or_dropship=has_preorder_or_dropship,
        has_swimwear_items=has_swimwear_items,
        has_open_case=has_open_case,
        payment_pending_review=payment_pending_review,
    )

    # Terminal cancelled / refunded orders have no customer actions left.
    if (
        fulfillment_status
        in (
            OrderFulfillmentStatus.CANCELLED,
            OrderFulfillmentStatus.RETURNED,
        )
        or payment_status == OrderPaymentStatus.REFUNDED
    ):
        return elig

    # 1. Unpaid / payment failed / bank-proof pending review
    if payment_status in (
        OrderPaymentStatus.UNPAID,
        OrderPaymentStatus.FAILED,
        OrderPaymentStatus.PENDING_REVIEW,
    ):
        if payment_pending_review:
            elig.can_self_cancel_unpaid = False
            elig.can_request_cancel = not has_open_cancel
            elig.cancel_block_reason = (
                "Your bank transfer receipt is currently under admin review. "
                "Submit a cancellation request so finance can reconcile your transfer."
            )
            elig.policy_notes.append(elig.cancel_block_reason)
        else:
            elig.can_self_cancel_unpaid = True
            elig.can_swap_variant = len(items) > 0
            elig.policy_notes.append(
                "Unpaid orders can be cancelled immediately or have a same-price size/color changed."
            )
        return elig

    # 2. Paid & awaiting preparation (before packing starts)
    if fulfillment_status == OrderFulfillmentStatus.AWAITING_PREPARATION:
        elig.can_swap_variant = len(items) > 0 and not has_open_cancel
        if has_preorder_or_dropship:
            elig.can_self_cancel_paid = False
            elig.can_request_cancel = not has_open_cancel
            elig.cancel_block_reason = (
                "This order includes preorder or dropship items. Submit a cancellation "
                "request so our team can confirm supplier order status (reviewed within 1 business day)."
            )
            elig.policy_notes.append(elig.cancel_block_reason)
        else:
            elig.can_self_cancel_paid = not has_open_cancel
            elig.policy_notes.append(
                "You can self-cancel this order before preparation starts for a full refund to your original payment method."
            )
        return elig

    # 3. Being prepared or ready for pool pickup
    if fulfillment_status in (
        OrderFulfillmentStatus.PROCESSING,
        OrderFulfillmentStatus.READY_FOR_PICKUP,
    ):
        elig.can_request_cancel = not has_open_cancel
        elig.policy_notes.append(
            "Preparation has started. Submit a cancellation request and our team will review it within 1 business day."
        )
        return elig

    # 4. Shipped / in transit
    if fulfillment_status == OrderFulfillmentStatus.SHIPPED:
        elig.cancel_block_reason = "Your order has already been dispatched. Once delivered, you can request a 7-day exchange/return or report a defect."
        elig.policy_notes.append(elig.cancel_block_reason)
        return elig

    # 5. Picked up or Delivered — 7-day voluntary return/exchange + FCCPA defect claims
    if fulfillment_status in (
        OrderFulfillmentStatus.PICKED_UP,
        OrderFulfillmentStatus.DELIVERED,
    ):
        handover_at = _ensure_aware(
            getattr(order, "delivered_at", None)
            or getattr(order, "fulfilled_at", None)
            or getattr(order, "updated_at", None)
        )
        if handover_at is not None:
            window_end = handover_at + timedelta(days=RETURN_WINDOW_DAYS)
            elig.return_window_ends_at = window_end
            in_return_window = now_aware <= window_end
            in_defect_window = now_aware <= (
                handover_at + timedelta(days=DEFECT_CLAIM_WINDOW_DAYS)
            )
        else:
            in_return_window = True
            in_defect_window = True

        if in_return_window and not has_open_return_or_defect:
            elig.can_request_return_exchange = True
            elig.policy_notes.append(
                "Within 7 days of handover, unused items qualify for size/color exchange, Store Credit, or Bubbles. Original delivery fee is non-refundable."
            )
            if has_swimwear_items:
                elig.policy_notes.append(
                    "Swimwear change-of-mind returns/exchanges require items to be unworn with hygiene seals and original tags intact."
                )

        if in_defect_window and not has_open_return_or_defect:
            elig.can_report_defect = True
            elig.policy_notes.append(
                "Received a defective, damaged, or wrong item? Statutory FCCPA remedies apply regardless of swimwear hygiene restrictions — full cash refund (including delivery fee) or free replacement."
            )

    return elig
