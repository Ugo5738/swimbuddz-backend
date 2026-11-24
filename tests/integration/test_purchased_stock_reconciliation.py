"""Exercise the scoped correction only in an explicitly isolated local database."""

import os
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from scripts.ops import reconcile_purchased_stock_20261124 as repair
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.store_service.models import (
    InventoryItem,
    InventoryMovement,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductVariant,
    SourcingType,
    StoreAuditLog,
)
from services.store_service.models.procurement import StoreCostEvent
from services.store_service.routers.admin_inventory import _helpers


@pytest_asyncio.fixture
async def case():
    url = os.environ.get("STOCK_RECONCILIATION_TEST_URL")
    if not url:
        pytest.skip("Set STOCK_RECONCILIATION_TEST_URL to isolated local PostgreSQL")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        schema = "stock_test_" + uuid.uuid4().hex
        await conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        await conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
        await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db:
            by_prefix = dict(
                zip(
                    [
                        "SB-GER-003",
                        "SB-GER-010",
                        "SB-GER-001",
                        "SB-TRN-007",
                        "SB-GER-009",
                        "SB-GER-004",
                        "SB-TRN-009",
                        "SB-TRN-004",
                    ],
                    [
                        "319d19b2-35f8-403d-bdb0-239e42350a51",
                        "c95fb22b-d9c2-4a11-93c1-473566903a86",
                        "1112c16f-4041-4142-a1c9-2891cdd7d19c",
                        "5c69c04a-ebf4-425f-9e18-f7c4ba6c0147",
                        "e1f6f48f-ef1e-47cb-8b82-462d79e5cf0b",
                        "5d1b088c-582e-42d2-b835-30a0182485a7",
                        "eb7e5b0b-59b5-4c6b-ab5e-531872464381",
                        "5574b760-6942-4ea4-9b9b-5379b25dbddd",
                    ],
                )
            )
            for prefix, pid in by_prefix.items():
                db.add(
                    Product(
                        id=uuid.UUID(pid),
                        name=prefix,
                        slug=prefix.lower(),
                        base_price_ngn=15000,
                        sourcing_type=SourcingType.PREORDER,
                    )
                )
            await db.flush()
            for vid, sku in repair.MANIFEST.items():
                db.add(
                    ProductVariant(
                        id=uuid.UUID(vid),
                        product_id=uuid.UUID(by_prefix[sku[:10]]),
                        sku=sku,
                        name=sku,
                    )
                )
            sibling = uuid.uuid4()
            db.add(
                ProductVariant(
                    id=sibling,
                    product_id=uuid.UUID(by_prefix["SB-GER-003"]),
                    sku="unpurchased-sibling",
                    name="Sibling",
                )
            )
            await db.flush()
            for vid in [uuid.UUID(v) for v in repair.MANIFEST] + [sibling]:
                db.add(
                    InventoryItem(
                        variant_id=vid, quantity_on_hand=0, quantity_reserved=0
                    )
                )
            db.add(
                Order(
                    id=repair.ORDER_ID,
                    order_number=repair.ORDER_NUMBER,
                    customer_name="Test Fatima",
                    customer_email="test@example.invalid",
                    subtotal_ngn=30000,
                    total_ngn=30000,
                    payment_reference=repair.PAYMENT_REF,
                    status=OrderStatus.SHIPPED,
                )
            )
            await db.flush()
            for lid, vid in repair.FATIMA_LINES.items():
                db.add(
                    OrderItem(
                        id=lid,
                        order_id=repair.ORDER_ID,
                        variant_id=vid,
                        product_name="Test product",
                        sku=repair.MANIFEST[str(vid)],
                        quantity=1,
                        unit_price_ngn=15000,
                        line_total_ngn=15000,
                        is_preorder=True,
                    )
                )
            db.add(
                Payment(
                    reference=repair.PAYMENT_REF,
                    member_auth_id=str(uuid.uuid4()),
                    purpose=PaymentPurpose.STORE_ORDER,
                    amount=30000,
                    currency="NGN",
                    payment_method="bank_transfer",
                    status=PaymentStatus.PAID,
                    entitlement_applied_at=utc_now(),
                    proof_of_payment_media_id=uuid.uuid4(),
                )
            )
            await db.commit()
            yield db, sibling
        await transaction.rollback()
    await engine.dispose()


async def counts(db):
    db.expire_all()
    return {
        str(i.variant_id): (i.quantity_on_hand, i.quantity_reserved)
        for i in (await db.scalars(select(InventoryItem))).all()
    }


@pytest.mark.asyncio
async def test_preview_apply_complete_retry_preserves_history_and_cash(
    case, monkeypatch
):
    db, sibling = case
    payment = await db.scalar(select(Payment))
    original_payment = (
        payment.amount,
        payment.status,
        payment.proof_of_payment_media_id,
        payment.reference,
    )
    preview = await repair.prepare(db)
    assert preview["dry_run"] and preview["variants"] == 13 and preview["products"] == 8
    assert set((await counts(db)).values()) == {(0, 0)}
    assert not (await db.scalars(select(StoreAuditLog))).all()
    await repair.prepare(db, apply=True, actor_label="owner-authorized test")
    await db.commit()
    prepared = await counts(db)
    assert prepared[str(sibling)] == (0, 0)
    assert {prepared[str(v)] for v in repair.FATIMA_LINES.values()} == {(6, 1)}
    assert all(
        p.sourcing_type == SourcingType.STOCKED
        for p in (await db.scalars(select(Product))).all()
    )
    monkeypatch.setattr(_helpers, "dispatch_notification", AsyncMock())
    monkeypatch.setattr(_helpers, "emit_rewards_event", AsyncMock())
    order = await _helpers._load_admin_order(db, repair.ORDER_ID)
    await _helpers._apply_order_status_change(
        db,
        order,
        OrderStatus.PICKED_UP,
        repair.REASON,
        AuthUser(user_id="service:stock-test", role="service_role"),
    )
    final = await counts(db)
    assert {final[v] for v in repair.MANIFEST} == {(5, 0)}
    assert final[str(sibling)] == (0, 0)
    assert (await db.scalar(select(Order))).status == OrderStatus.PICKED_UP
    assert all(i.is_preorder for i in (await db.scalars(select(OrderItem))).all())
    assert not (await db.scalars(select(StoreCostEvent))).all()
    payment = await db.scalar(select(Payment))
    assert (
        payment.amount,
        payment.status,
        payment.proof_of_payment_media_id,
        payment.reference,
    ) == original_payment
    movements_before = len((await db.scalars(select(InventoryMovement))).all())
    # Later sales must not be reset to the reviewed opening quantity on replay.
    await db.execute(
        text(
            "UPDATE store_inventory_items SET quantity_on_hand=4 WHERE variant_id=:id"
        ),
        {"id": next(iter(repair.FATIMA_LINES.values()))},
    )
    await db.commit()
    assert (await repair.prepare(db, apply=True, actor_label="retry"))[
        "already_prepared"
    ]
    assert 4 in [v[0] for v in (await counts(db)).values()]
    assert (
        len((await db.scalars(select(InventoryMovement))).all())
        == movements_before
        == 17
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,expected",
    [
        (
            "UPDATE store_inventory_items SET quantity_on_hand=1",
            "Stock/sourcing changed",
        ),
        ("UPDATE payments SET status='pending_review'", "must be verified"),
        ("UPDATE store_orders SET status='cancelled'", "handover state changed"),
        ("UPDATE store_order_items SET quantity=2", "preorder lines changed"),
    ],
)
async def test_changed_state_blocks_without_repair_writes(case, change, expected):
    db, _ = case
    await db.execute(text(change))
    await db.commit()
    with pytest.raises(ValueError, match=expected):
        await repair.prepare(db, apply=True, actor_label="test")
    assert not (await db.scalars(select(StoreAuditLog))).all()
    assert not (await db.scalars(select(InventoryMovement))).all()
    assert all(
        p.sourcing_type == SourcingType.PREORDER
        for p in (await db.scalars(select(Product))).all()
    )


@pytest.mark.asyncio
async def test_apply_needs_attribution(case):
    db, _ = case
    with pytest.raises(ValueError, match="actor-label"):
        await repair.prepare(db, apply=True)


@pytest.mark.asyncio
async def test_completed_api_replay_does_not_patch(monkeypatch):
    from libs.common import service_client

    response = AsyncMock()
    response.raise_for_status = lambda: None
    response.json = lambda: {"status": "picked_up"}
    patch = AsyncMock()
    monkeypatch.setattr(
        service_client, "internal_get", AsyncMock(return_value=response)
    )
    monkeypatch.setattr(service_client, "internal_patch", patch)
    assert (await repair.complete())["already_completed"]
    patch.assert_not_awaited()
