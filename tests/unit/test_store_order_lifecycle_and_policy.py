"""Unit tests for Store post-purchase order lifecycle, policy engine, and cases (ADR-024)."""

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from libs.auth.models import AuthUser
from services.store_service.models.enums import (
    OrderCaseResolution,
    OrderFulfillmentStatus,
    OrderPaymentStatus,
    OrderStatus,
    ReturnInspectionCondition,
)
from services.store_service.routers.admin_inventory import _helpers
from services.store_service.services.order_lifecycle import (
    restock_returned_case_items,
    restore_order_store_credits,
    swap_order_item_variant,
)
from services.store_service.services.order_policy import (
    allocate_line_net_totals,
    compute_order_refund_tenders,
    get_order_action_eligibility,
)


def _make_order(
    *,
    status=OrderStatus.PAID,
    payment_status=OrderPaymentStatus.PAID,
    fulfillment_status=OrderFulfillmentStatus.AWAITING_PREPARATION,
    subtotal=Decimal("25000.00"),
    discount=Decimal("0.00"),
    delivery_fee=Decimal("2000.00"),
    store_credit=Decimal("0.00"),
    bubbles=0,
    total_ngn=None,
    items=None,
    delivered_at=None,
    fulfilled_at=None,
    paid_at=None,
):
    order_total = subtotal - discount + delivery_fee
    if total_ngn is None:
        total_ngn = order_total - store_credit - Decimal(bubbles * 100)
    return SimpleNamespace(
        id=uuid.uuid4(),
        order_number="SB-20261125-ABC12",
        member_auth_id="user-1",
        status=status,
        payment_status=payment_status,
        fulfillment_status=fulfillment_status,
        effective_payment_status=payment_status,
        effective_fulfillment_status=fulfillment_status,
        subtotal_ngn=subtotal,
        discount_amount_ngn=discount,
        delivery_fee_ngn=delivery_fee,
        store_credit_applied_ngn=store_credit,
        bubbles_applied=bubbles,
        order_total_ngn=order_total,
        effective_order_total_ngn=order_total,
        total_ngn=total_ngn,
        items=items
        or [
            SimpleNamespace(
                id=uuid.uuid4(),
                variant_id=uuid.uuid4(),
                quantity=1,
                unit_price_ngn=Decimal("25000.00"),
                line_total_ngn=Decimal("25000.00"),
                is_preorder=False,
                sourcing_type="stocked",
                requires_size_chart_ack=False,
                variant=None,
            )
        ],
        delivered_at=delivered_at,
        fulfilled_at=fulfilled_at,
        paid_at=paid_at or datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


class TestLineAllocationAndRefundTenders:
    def test_allocate_line_net_totals_conserves_exact_amount(self):
        lines = allocate_line_net_totals(
            [Decimal("15000.00"), Decimal("10000.00")],
            Decimal("2500.00"),
        )
        assert lines == [Decimal("13500.00"), Decimal("9000.00")]
        assert sum(lines) == Decimal("22500.00")

    def test_allocate_line_net_totals_handles_odd_split_without_rounding_drift(self):
        lines = allocate_line_net_totals(
            [Decimal("10000.00"), Decimal("10000.00"), Decimal("10000.00")],
            Decimal("1000.00"),
        )
        assert sum(lines) == Decimal("29000.00")

    def test_full_refund_returns_exact_multi_tender_breakdown(self):
        # ₦25k subtotal - ₦2.5k discount + ₦2k delivery = ₦24.5k net order
        # Paid with ₦3k Store Credit + 50 Bubbles (₦5k) + ₦16.5k cash
        order = _make_order(
            subtotal=Decimal("25000.00"),
            discount=Decimal("2500.00"),
            delivery_fee=Decimal("2000.00"),
            store_credit=Decimal("3000.00"),
            bubbles=50,
            total_ngn=Decimal("16500.00"),
        )
        calc = compute_order_refund_tenders(
            order,
            include_delivery_fee=True,
            resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
        )
        assert calc.cash_refund_kobo == 1_650_000
        assert calc.bubbles_refund == 50
        assert calc.store_credit_refund_kobo == 300_000
        assert calc.total_refund_kobo == 2_450_000  # Never refunds the ₦2.5k discount!

    def test_100_percent_bubbles_order_still_refunds_bubbles_when_total_ngn_zero(self):
        order = _make_order(
            subtotal=Decimal("15000.00"),
            delivery_fee=Decimal("0.00"),
            bubbles=150,
            total_ngn=Decimal("0.00"),
        )
        calc = compute_order_refund_tenders(
            order,
            include_delivery_fee=True,
            resolution=OrderCaseResolution.FULL_REFUND_ORIGINAL_METHOD,
        )
        assert calc.cash_refund_kobo == 0
        assert calc.bubbles_refund == 150
        assert calc.total_refund_kobo == 1_500_000

    def test_change_of_mind_store_credit_excludes_delivery_fee_and_zeroes_cash(self):
        order = _make_order(
            subtotal=Decimal("20000.00"),
            delivery_fee=Decimal("2000.00"),
            total_ngn=Decimal("22000.00"),
        )
        calc = compute_order_refund_tenders(
            order,
            include_delivery_fee=False,
            resolution=OrderCaseResolution.STORE_CREDIT,
        )
        assert calc.cash_refund_kobo == 0
        assert calc.bubbles_refund == 0
        assert calc.store_credit_refund_kobo == 2_000_000

    def test_wallet_bubbles_resolution_preserves_sub_100_naira_remainder_as_credit(
        self,
    ):
        order = _make_order(
            subtotal=Decimal("15050.00"),
            delivery_fee=Decimal("0.00"),
            total_ngn=Decimal("15050.00"),
        )
        calc = compute_order_refund_tenders(
            order,
            include_delivery_fee=False,
            resolution=OrderCaseResolution.WALLET_BUBBLES,
        )
        assert calc.cash_refund_kobo == 0
        assert calc.bubbles_refund == 150
        assert calc.store_credit_refund_kobo == 5_000  # ₦50 preserved, never dropped


class TestOrderActionEligibility:
    def test_unpaid_order_self_cancel_allowed_unless_bank_proof_pending_review(self):
        now = datetime.now(timezone.utc)
        unpaid = _make_order(
            status=OrderStatus.PENDING_PAYMENT,
            payment_status=OrderPaymentStatus.UNPAID,
            fulfillment_status=OrderFulfillmentStatus.NOT_STARTED,
        )
        elig_normal = get_order_action_eligibility(unpaid, now=now)
        assert elig_normal.can_self_cancel_unpaid is True
        assert elig_normal.can_swap_variant is True

        elig_pending_proof = get_order_action_eligibility(
            unpaid,
            now=now,
            payment_status_info={"status": "pending_review", "has_proof": True},
        )
        assert elig_pending_proof.can_self_cancel_unpaid is False
        assert elig_pending_proof.can_request_cancel is True
        assert elig_pending_proof.payment_pending_review is True

    def test_paid_stocked_order_awaiting_prep_allows_instant_self_cancel_and_swap(self):
        now = datetime.now(timezone.utc)
        order = _make_order()
        elig = get_order_action_eligibility(order, now=now)
        assert elig.can_self_cancel_paid is True
        assert elig.can_swap_variant is True
        assert elig.can_request_cancel is False

    def test_paid_preorder_or_dropship_order_is_request_only(self):
        now = datetime.now(timezone.utc)
        preorder_item = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            quantity=1,
            is_preorder=True,
            sourcing_type="preorder",
            requires_size_chart_ack=False,
            variant=None,
        )
        order = _make_order(items=[preorder_item])
        elig = get_order_action_eligibility(order, now=now)
        assert elig.has_preorder_or_dropship is True
        assert elig.can_self_cancel_paid is False
        assert elig.can_request_cancel is True

    def test_processing_and_ready_for_pickup_are_request_only(self):
        now = datetime.now(timezone.utc)
        for st, fs in (
            (OrderStatus.PROCESSING, OrderFulfillmentStatus.PROCESSING),
            (OrderStatus.READY_FOR_PICKUP, OrderFulfillmentStatus.READY_FOR_PICKUP),
        ):
            order = _make_order(status=st, fulfillment_status=fs)
            elig = get_order_action_eligibility(order, now=now)
            assert elig.can_self_cancel_paid is False
            assert elig.can_request_cancel is True
            assert elig.can_swap_variant is False

    def test_shipped_order_disables_cancellation(self):
        now = datetime.now(timezone.utc)
        order = _make_order(
            status=OrderStatus.SHIPPED,
            fulfillment_status=OrderFulfillmentStatus.SHIPPED,
        )
        elig = get_order_action_eligibility(order, now=now)
        assert elig.can_self_cancel_paid is False
        assert elig.can_request_cancel is False

    def test_delivered_order_7_day_return_window_and_30_day_fccpa_defect_claim(self):
        now = datetime.now(timezone.utc)
        swimsuit = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            quantity=1,
            is_preorder=False,
            sourcing_type="stocked",
            requires_size_chart_ack=True,
            variant=None,
        )
        recent = _make_order(
            status=OrderStatus.DELIVERED,
            fulfillment_status=OrderFulfillmentStatus.DELIVERED,
            delivered_at=now - timedelta(days=3),
            items=[swimsuit],
        )
        elig_recent = get_order_action_eligibility(recent, now=now)
        assert elig_recent.can_request_return_exchange is True
        assert elig_recent.can_report_defect is True
        assert elig_recent.has_swimwear_items is True
        assert any("hygiene seals" in note for note in elig_recent.policy_notes)

        day_10 = _make_order(
            status=OrderStatus.DELIVERED,
            fulfillment_status=OrderFulfillmentStatus.DELIVERED,
            delivered_at=now - timedelta(days=10),
            items=[swimsuit],
        )
        elig_day_10 = get_order_action_eligibility(day_10, now=now)
        assert elig_day_10.can_request_return_exchange is False
        assert (
            elig_day_10.can_report_defect is True
        )  # FCCPA statutory defect claim still open!


class TestLifecycleGuardsAndAtomics:
    @pytest.mark.asyncio
    async def test_admin_raw_status_dropdown_blocks_paid_order_cancellation(self):
        order = _make_order(status=OrderStatus.PAID)
        db = SimpleNamespace(commit=AsyncMock())
        admin = AuthUser(sub="admin-1", role="admin")
        with pytest.raises(HTTPException) as err:
            await _helpers._apply_order_status_change(
                db, order, OrderStatus.CANCELLED, "nope", admin
            )
        assert err.value.status_code == 409
        assert "Cancel & Issue Refund" in err.value.detail
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swap_variant_rejects_price_mismatch(self):
        product_id = uuid.uuid4()
        old_vid = uuid.uuid4()
        new_vid = uuid.uuid4()
        old_variant = SimpleNamespace(
            id=old_vid,
            product_id=product_id,
            sku="SWIM-M",
            name="Size M",
            is_active=True,
            price_override_ngn=None,
            product=SimpleNamespace(base_price_ngn=Decimal("15000.00")),
        )
        new_variant = SimpleNamespace(
            id=new_vid,
            product_id=product_id,
            sku="SWIM-XL",
            name="Size XL",
            is_active=True,
            price_override_ngn=Decimal("18000.00"),
            product=SimpleNamespace(base_price_ngn=Decimal("15000.00")),
        )
        order_item = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=old_vid,
            variant_name="Size M",
            sku="SWIM-M",
            quantity=1,
            unit_price_ngn=Decimal("15000.00"),
            is_preorder=False,
            sourcing_type="stocked",
        )
        order = _make_order(items=[order_item])
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(scalar_one_or_none=lambda: old_variant)
            )
        )
        with pytest.raises(HTTPException) as err:
            await swap_order_item_variant(
                db, order, order_item, new_variant, performed_by="user-1"
            )
        assert err.value.status_code == 422
        assert "exact same price" in err.value.detail

    @pytest.mark.asyncio
    async def test_restock_returned_items_only_restocks_when_condition_is_restockable(
        self,
    ):
        order = _make_order()
        case = SimpleNamespace(
            id=uuid.uuid4(),
            case_number="CASE-20261125-00001",
            restocked_at=None,
        )
        db = SimpleNamespace(execute=AsyncMock(), add=lambda _obj: None)
        units_hygiene = await restock_returned_case_items(
            db,
            order,
            case,
            return_condition=ReturnInspectionCondition.HYGIENE_UNSELLABLE,
            performed_by="admin-1",
        )
        assert units_hygiene == 0
        assert case.restocked_at is None
        db.execute.assert_not_awaited()

        units_damaged = await restock_returned_case_items(
            db,
            order,
            case,
            return_condition=ReturnInspectionCondition.DAMAGED,
            performed_by="admin-1",
        )
        assert units_damaged == 0
        assert case.restocked_at is None

    @pytest.mark.asyncio
    async def test_restore_store_credits_is_idempotent(self):
        credit_id = uuid.uuid4()
        order = _make_order(store_credit=Decimal("4000.00"))
        existing_restore = SimpleNamespace(store_credit_id=credit_id)
        credit_obj = SimpleNamespace(id=credit_id, balance_ngn=Decimal("4000.00"))
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(first=lambda: existing_restore)
                )
            ),
            get=AsyncMock(return_value=credit_obj),
            add=lambda _obj: (_ for _ in ()).throw(
                AssertionError("Must not double-add")
            ),
        )
        res = await restore_order_store_credits(db, order, "user-1")
        assert res is credit_obj
        assert credit_obj.balance_ngn == Decimal("4000.00")

    def test_store_app_mounts_order_actions_and_admin_cases_routes(self):
        from services.store_service.app.main import app

        paths = {route.path for route in app.routes}
        assert "/store/orders/{order_number}/actions" in paths
        assert "/store/orders/{order_number}/cancel" in paths
        assert "/store/orders/{order_number}/swap-variant" in paths
        assert "/store/orders/{order_number}/cases" in paths
        assert "/admin/store/cases" in paths
        assert "/admin/store/cases/{case_id}/review" in paths
        assert "/admin/store/cases/{case_id}/receive-return" in paths
        assert "/admin/store/orders/{order_id}/cancel-and-refund" in paths
        assert "/internal/store/orders/refund-status-callback" in paths


class TestFulfillmentStockConsumption:
    @pytest.mark.asyncio
    async def test_stocked_order_consumes_reserved_and_on_hand(self):
        from services.store_service.models.enums import SourcingType

        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Goggles",
            variant_name="Black",
            quantity=1,
            is_preorder=False,
            sourcing_type="stocked",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.STOCKED)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=5, quantity_reserved=1)
        added = []
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(side_effect=[inv, None]),
            add=added.append,
        )

        cogs = await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert cogs == 0
        assert inv.quantity_on_hand == 4
        assert inv.quantity_reserved == 0
        assert any(
            getattr(obj, "movement_type", None) is not None and obj.quantity == -1
            for obj in added
        )

    @pytest.mark.asyncio
    async def test_preorder_era_line_on_now_stocked_product_consumes_unreserved_only(
        self,
    ):
        from services.store_service.models.enums import SourcingType

        # Mimics Feji (#SB-20261123-C47IO): ordered while preorder (is_preorder=True,
        # never incremented quantity_reserved), product later switched to stocked with
        # 5 on hand — and another stocked customer currently holds 1 reservation.
        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Electroplated Goggles",
            variant_name="Black",
            quantity=1,
            is_preorder=True,
            sourcing_type="preorder",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.STOCKED)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=5, quantity_reserved=1)
        added = []
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            # scalar calls: 1) inventory lock, 2) explicit order reservation sum (0), 3) valuation (None)
            scalar=AsyncMock(side_effect=[inv, 0, None]),
            add=added.append,
        )

        await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert inv.quantity_on_hand == 4
        # Other customer's reservation MUST stay intact!
        assert inv.quantity_reserved == 1

    @pytest.mark.asyncio
    async def test_preorder_era_line_with_explicit_order_reservation_consumes_both(
        self,
    ):
        from services.store_service.models.enums import SourcingType

        # Mimics Fatima reconciliation: is_preorder=True + explicit order RESERVATION (+1).
        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Kickboard",
            variant_name="Orange",
            quantity=1,
            is_preorder=True,
            sourcing_type="preorder",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.STOCKED)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=6, quantity_reserved=1)
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(side_effect=[inv, 1, None]),
            add=lambda _obj: None,
        )

        await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert inv.quantity_on_hand == 5
        assert inv.quantity_reserved == 0

    @pytest.mark.asyncio
    async def test_preorder_product_with_zero_stock_is_blocked_strictly_option_a(self):
        from services.store_service.models.enums import SourcingType

        # Industry standard Option A: a preorder batch must be received into
        # Procurement / Inventory before handover so stock & COGS are tracked.
        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Training Snorkel",
            variant_name="Blue",
            quantity=1,
            is_preorder=True,
            sourcing_type="preorder",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.PREORDER)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=0, quantity_reserved=0)
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(side_effect=[inv, 0]),
            add=lambda _obj: None,
        )

        with pytest.raises(HTTPException) as exc:
            await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert exc.value.status_code == 409
        assert "Training Snorkel (Blue)" in exc.value.detail
        assert "Goods Receipt or Inventory Restock" in exc.value.detail

    @pytest.mark.asyncio
    async def test_dropship_line_skips_warehouse_stock_and_emits_sale_unvalued_cost_event(
        self,
    ):
        from services.store_service.models.enums import SourcingType

        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Dropship Fins",
            variant_name="L",
            quantity=2,
            is_preorder=False,
            sourcing_type="dropship",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.DROPSHIP)
            ),
        )
        added = []
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(),
            add=added.append,
        )

        cogs = await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert cogs == 0
        db.scalar.assert_not_awaited()
        assert len(added) == 1
        assert added[0].event_kind == "sale_unvalued"
        assert added[0].quantity_delta == -2
        assert f"dropship-fulfilled-order:{order.id}" == added[0].evidence

    @pytest.mark.asyncio
    async def test_partial_fifo_preorder_consumes_its_reserved_plus_unreserved_without_stealing_others(
        self,
    ):
        from services.store_service.models.enums import SourcingType

        # Line needs 2 units; 1 was FIFO-reserved for this order, 1 is reserved for
        # ANOTHER customer, and 1 unit is unreserved on hand (total on_hand=3, reserved=2).
        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Training Snorkel",
            variant_name="Blue",
            quantity=2,
            is_preorder=True,
            sourcing_type="preorder",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.PREORDER)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=3, quantity_reserved=2)
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(side_effect=[inv, 1, None]),
            add=lambda _obj: None,
        )

        await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert inv.quantity_on_hand == 1
        assert inv.quantity_reserved == 1  # Other customer's 1 reservation preserved!

    @pytest.mark.asyncio
    async def test_preorder_era_line_on_stocked_product_with_zero_available_raises_409(
        self,
    ):
        from services.store_service.models.enums import SourcingType

        order = _make_order()
        line = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            product_name="Training Snorkel",
            variant_name="Blue",
            quantity=1,
            is_preorder=True,
            sourcing_type="preorder",
            variant=SimpleNamespace(
                product=SimpleNamespace(sourcing_type=SourcingType.STOCKED)
            ),
        )
        inv = SimpleNamespace(id=uuid.uuid4(), quantity_on_hand=1, quantity_reserved=1)
        db = SimpleNamespace(
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [line])),
            scalar=AsyncMock(side_effect=[inv, 0]),
            add=lambda _obj: None,
        )

        with pytest.raises(HTTPException) as exc:
            await _helpers._consume_fulfilled_order_stock(db, order, "admin-1")
        assert exc.value.status_code == 409
        assert "Training Snorkel (Blue)" in exc.value.detail
        assert "0 unreserved" in exc.value.detail


class TestFifoPreorderAllocation:
    @pytest.mark.asyncio
    async def test_arriving_stock_allocates_fifo_across_paid_preorders_including_partial_batch(
        self,
    ):
        from services.store_service.services.preorder_allocation import (
            allocate_stock_to_paid_preorders,
        )

        inv = SimpleNamespace(
            id=uuid.uuid4(),
            variant_id=uuid.uuid4(),
            quantity_on_hand=3,
            quantity_reserved=0,
        )
        order_1_id = uuid.uuid4()
        order_2_id = uuid.uuid4()
        fifo_rows = [
            (
                order_1_id,
                "SB-PRE-001",
                2,
                datetime(2026, 11, 20),
                datetime(2026, 11, 20),
            ),
            (
                order_2_id,
                "SB-PRE-002",
                2,
                datetime(2026, 11, 22),
                datetime(2026, 11, 22),
            ),
        ]
        added = []
        db = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: fifo_rows)),
            # Batch 1: neither order has prior reservation (0, 0)
            scalar=AsyncMock(side_effect=[0, 0]),
            add=added.append,
        )

        allocated_batch_1 = await allocate_stock_to_paid_preorders(
            db, inv, "admin-1", reason="PO-101"
        )
        assert allocated_batch_1 == 3
        assert inv.quantity_reserved == 3  # 0 available to new buyers!
        assert [(m.reference_id, m.quantity) for m in added] == [
            (order_1_id, 2),
            (order_2_id, 1),
        ]

        # Batch 2 arrives: +4 units (on_hand: 3 -> 7, available = 4)
        inv.quantity_on_hand = 7
        added_batch_2 = []
        db2 = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(all=lambda: fifo_rows)),
            # Order 1 already has 2/2 reserved; Order 2 has 1/2 reserved
            scalar=AsyncMock(side_effect=[2, 1]),
            add=added_batch_2.append,
        )
        allocated_batch_2 = await allocate_stock_to_paid_preorders(
            db2, inv, "admin-1", reason="PO-102"
        )
        assert allocated_batch_2 == 1
        assert inv.quantity_reserved == 4  # 3 leftover units go on sale!
        assert [(m.reference_id, m.quantity) for m in added_batch_2] == [
            (order_2_id, 1)
        ]


class TestVariantBackorderCheckoutPlanning:
    def test_stocked_variant_in_stock_reserves_full_quantity(self):
        from services.store_service.models.enums import SourcingType
        from services.store_service.routers.orders import _plan_checkout_line_stock

        product = SimpleNamespace(sourcing_type=SourcingType.STOCKED)
        variant = SimpleNamespace(sku="CAP-BLU", allow_backorder=False)
        inv = SimpleNamespace(quantity_available=5)

        is_pre, sourcing, reserve_now, use_order_ref = _plan_checkout_line_stock(
            variant, product, inv, 2
        )
        assert (is_pre, sourcing, reserve_now, use_order_ref) == (
            False,
            "stocked",
            2,
            False,
        )

    def test_stocked_variant_out_of_stock_without_backorder_raises_400(self):
        from services.store_service.models.enums import SourcingType
        from services.store_service.routers.orders import _plan_checkout_line_stock

        product = SimpleNamespace(sourcing_type=SourcingType.STOCKED)
        variant = SimpleNamespace(sku="CAP-BLK", allow_backorder=False)
        inv = SimpleNamespace(quantity_available=0)

        with pytest.raises(HTTPException) as exc:
            _plan_checkout_line_stock(variant, product, inv, 1)
        assert exc.value.status_code == 400
        assert "Only 0 available" in exc.value.detail

    def test_stocked_variant_zero_stock_with_allow_backorder_becomes_preorder(self):
        from services.store_service.models.enums import SourcingType
        from services.store_service.routers.orders import _plan_checkout_line_stock

        product = SimpleNamespace(sourcing_type=SourcingType.STOCKED)
        variant = SimpleNamespace(sku="CAP-BLK", allow_backorder=True)
        inv = SimpleNamespace(quantity_available=0)

        is_pre, sourcing, reserve_now, use_order_ref = _plan_checkout_line_stock(
            variant, product, inv, 2
        )
        assert (is_pre, sourcing, reserve_now, use_order_ref) == (
            True,
            "preorder",
            0,
            True,
        )

    def test_stocked_variant_partial_stock_with_allow_backorder_reserves_available_order_scoped(
        self,
    ):
        from services.store_service.models.enums import SourcingType
        from services.store_service.routers.orders import _plan_checkout_line_stock

        # 1 unit available on hand, customer buys 3 with allow_backorder=True:
        # reserve the 1 unit against order_id now; remaining 2 wait in FIFO queue.
        product = SimpleNamespace(sourcing_type=SourcingType.STOCKED)
        variant = SimpleNamespace(sku="CAP-BLU", allow_backorder=True)
        inv = SimpleNamespace(quantity_available=1)

        is_pre, sourcing, reserve_now, use_order_ref = _plan_checkout_line_stock(
            variant, product, inv, 3
        )
        assert (is_pre, sourcing, reserve_now, use_order_ref) == (
            True,
            "preorder",
            1,
            True,
        )
