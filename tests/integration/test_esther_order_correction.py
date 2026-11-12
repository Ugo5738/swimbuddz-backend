"""Run against an explicitly isolated local PG; never the configured app DB."""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from scripts.ops import correct_esther_goggles_20261112 as repair
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.store_service.models import (
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductVariant,
    SourcingType,
    StoreAuditLog,
    Supplier,
)


@pytest_asyncio.fixture
async def case():
    url = os.environ.get("ESTHER_CORRECTION_TEST_URL")
    if not url:
        pytest.skip("Set ESTHER_CORRECTION_TEST_URL to an isolated local PostgreSQL")
    assert make_url(url).host in {"localhost", "127.0.0.1"}
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        schema = "esther_test_" + uuid.uuid4().hex
        await conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
        await conn.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
        await conn.run_sync(Base.metadata.create_all)
        async with AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as db:
            supplier = Supplier(name="Test supplier", slug="test-supplier")
            db.add(supplier)
            await db.flush()
            products = []
            for n, price in enumerate([10000, 15000, 12000, 15000]):
                p = Product(
                    name=f"Product {n}",
                    slug=f"product-{n}",
                    base_price_ngn=price,
                    sourcing_type=SourcingType.PREORDER,
                    supplier_id=supplier.id,
                    preorder_lead_days=14,
                )
                db.add(p)
                products.append(p)
            await db.flush()
            variant_ids = [
                uuid.UUID(repair.OLD_VARIANT),
                uuid.UUID(repair.NEW_VARIANT),
                uuid.uuid4(),
                uuid.uuid4(),
            ]
            skus = [
                "SB-GER-007-BLA",
                "SB-GER-001-BLA",
                "SB-GER-009-BLA-80G",
                "SB-TRN-004-Blue",
            ]
            for p, vid, sku in zip(products, variant_ids, skus):
                db.add(
                    ProductVariant(
                        id=vid, product_id=p.id, sku=sku, name="Black", is_active=True
                    )
                )
            db.add(
                Order(
                    id=uuid.UUID(repair.ORDER_ID),
                    order_number="SB-20261111-FATLF",
                    member_auth_id=repair.AUTH_ID,
                    customer_name="Test Esther",
                    customer_email="esther@example.invalid",
                    subtotal_ngn=37000,
                    total_ngn=37000,
                    payment_reference=repair.ORIGINAL_REF,
                    status=OrderStatus.PENDING_PAYMENT,
                )
            )
            await db.flush()
            for n, (idx, price) in enumerate([(0, 10000), (2, 12000), (3, 15000)]):
                db.add(
                    OrderItem(
                        id=uuid.UUID(repair.ITEM_ID) if n == 0 else uuid.uuid4(),
                        order_id=uuid.UUID(repair.ORDER_ID),
                        variant_id=variant_ids[idx],
                        product_name=f"Product {idx}",
                        variant_name="Black",
                        sku=skus[idx],
                        quantity=1,
                        unit_price_ngn=price,
                        line_total_ngn=price,
                        is_preorder=True,
                    )
                )
            proof = uuid.uuid4()
            db.add(
                Payment(
                    reference=repair.ORIGINAL_REF,
                    member_auth_id=repair.AUTH_ID,
                    purpose=PaymentPurpose.STORE_ORDER,
                    amount=37000,
                    currency="NGN",
                    payment_method="manual_transfer",
                    status=PaymentStatus.PENDING_REVIEW,
                    proof_of_payment_media_id=proof,
                    payment_metadata={
                        "order_id": repair.ORDER_ID,
                        "submitted_transfer": {"received_date": "2026-11-11"},
                        "frozen_original_quote": {"amount": 37000},
                    },
                )
            )
            await db.commit()
            yield db, proof
        await transaction.rollback()
    await engine.dispose()


async def payments(db):
    db.expire_all()
    return list(
        (await db.scalars(select(Payment).order_by(Payment.amount.desc()))).all()
    )


async def stage(db):
    result = await repair.correct(db, apply=True, actor_label="owner-authorized test")
    await db.commit()
    return result


@pytest.mark.asyncio
async def test_dry_run_apply_repeat_preserves_receipt_and_no_cash(case):
    db, proof = case
    preview = await repair.correct(db)
    assert preview["total_ngn"] == "42000.00"
    assert len(await payments(db)) == 1
    await stage(db)
    assert (await repair.correct(db, apply=True, actor_label="retry"))[
        "already_applied"
    ]
    rows = await payments(db)
    assert [p.amount for p in rows] == [37000, 5000]
    assert rows[0].proof_of_payment_media_id == proof
    assert rows[0].payment_metadata["frozen_original_quote"] == {"amount": 37000}
    assert rows[0].payment_metadata["submitted_transfer"] == {
        "received_date": "2026-11-11"
    }
    assert all(
        p.status == PaymentStatus.PENDING_REVIEW and p.paid_at is None for p in rows
    )
    assert all(p.payment_metadata["checkout_reconciliation"] for p in rows)
    assert (
        rows[1].payment_metadata["submitted_transfer"]["external_reference"]
        == repair.BANK_REF
    )
    order = await db.get(Order, uuid.UUID(repair.ORDER_ID))
    item = await db.get(OrderItem, uuid.UUID(repair.ITEM_ID))
    assert order.total_ngn == 42000 and order.status == OrderStatus.PENDING_PAYMENT
    assert str(item.variant_id) == repair.NEW_VARIANT and item.line_total_ngn == 15000
    assert len((await db.scalars(select(StoreAuditLog))).all()) == 1
    assert await db.scalar(text("SELECT count(*) FROM store_inventory_movements")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE payments SET status='paid'",
        "UPDATE store_orders SET total_ngn=36000",
        "UPDATE store_order_items SET quantity=2 WHERE sku='SB-TRN-004-Blue'",
        "UPDATE store_product_variants SET price_override_ngn=16000 WHERE sku='SB-GER-001-BLA'",
        "UPDATE payments SET provider_reference='" + repair.BANK_REF + "'",
    ],
)
async def test_stale_or_duplicate_state_is_rejected(case, mutation):
    db, _ = case
    await db.execute(text(mutation))
    await db.commit()
    with pytest.raises(ValueError):
        await stage(db)
    assert len(await payments(db)) == 1


@pytest.mark.asyncio
async def test_failure_rolls_back_whole_repair(case):
    db, proof = case
    with patch.object(
        repair, "_audit", AsyncMock(side_effect=RuntimeError("audit failed"))
    ):
        with pytest.raises(RuntimeError):
            await stage(db)
    await db.rollback()
    assert len(await payments(db)) == 1
    assert (await payments(db))[0].proof_of_payment_media_id == proof
    assert (await db.get(Order, uuid.UUID(repair.ORDER_ID))).total_ngn == 37000


@pytest.mark.asyncio
async def test_paid_partial_cannot_fulfill_or_release(case):
    db, _ = case
    await stage(db)
    rows = await payments(db)
    rows[0].status = PaymentStatus.PAID
    rows[0].provider = "offline"
    rows[0].provider_reference = "original-bank-ref"
    rows[0].payment_method = "bank_transfer"
    rows[0].paid_at = utc_now()
    await db.commit()
    from services.payments_service.routers.intents._entitlement import _dispatcher

    with patch.object(_dispatcher, "_apply_entitlement", AsyncMock()) as fulfill:
        await _dispatcher._apply_entitlement_with_tracking(rows[0])
        fulfill.assert_not_awaited()
    with pytest.raises(ValueError, match="independent bank receipts"):
        await repair.correct(db, release=True, apply=True, actor_label="test")


@pytest.mark.asyncio
async def test_complete_receipts_release_primary_only_and_cash_totals_42000(case):
    db, _ = case
    await stage(db)
    rows = await payments(db)
    for p, ref in zip(rows, ["original-bank-ref", repair.BANK_REF]):
        p.status, p.provider, p.payment_method = (
            PaymentStatus.PAID,
            "offline",
            "bank_transfer",
        )
        p.provider_reference, p.paid_at = ref, utc_now()
    await db.commit()
    from services.payments_service.services.ledger_emit import build_post_kwargs

    assert sum(build_post_kwargs(p)["lines"][0]["debit"] for p in rows) == 4_200_000
    result = await repair.correct(db, release=True)
    assert result["posts_new_cash"] is False
    assert (await payments(db))[0].payment_metadata.get("checkout_reconciliation")
    await repair.correct(db, release=True, apply=True, actor_label="test")
    await db.commit()
    rows = await payments(db)
    assert "checkout_reconciliation" not in rows[0].payment_metadata
    assert rows[1].payment_metadata["checkout_reconciliation"]
    assert (await repair.correct(db, release=True, apply=True, actor_label="retry"))[
        "already_released"
    ]
    assert (
        await db.get(Order, uuid.UUID(repair.ORDER_ID))
    ).status == OrderStatus.PENDING_PAYMENT
