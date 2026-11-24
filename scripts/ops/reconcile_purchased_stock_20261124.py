"""Owner-reviewed stock count and Fatima pickup; dry-run by default.

See docs/STORE_STOCK_RECONCILIATION_20261124.md. This scoped maintenance
transaction reads the exact Payments settlement as a precondition but only
writes Store records. It never settles cash or invents procurement costs.
"""

import argparse
import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OPERATION = "purchased-stock-fatima-20261124"
OPERATION_ID = uuid.uuid5(uuid.NAMESPACE_URL, OPERATION)
ORDER_ID = uuid.UUID("5e968ae2-8214-45da-b85b-316ce556a940")
ORDER_NUMBER = "SB-20261121-VVAUF"
PAYMENT_REF = "store-order-5e968ae2-8214-45da-b85b-316ce556a940-090ec4b13226"
REMAINING = 5
# Exact purchased variants only; siblings keep their existing quantities.
MANIFEST = {
    "acd501f5-3649-41a3-bc75-40cd4bbeeb8c": "SB-GER-003-Blue",
    "3a31d93c-cf4f-41e5-b264-d2cd0bf58063": "SB-GER-010-Blue",
    "eabd73c7-2a93-4a2f-89f6-c868464f1d43": "SB-GER-010-Pink",
    "bbcdf0b7-e39a-4bab-90b1-9b65e1481b06": "SB-GER-001-BLA",
    "613ccef6-694f-43a7-ab51-8cde74e25f91": "SB-GER-001-Blue",
    "c3928584-f3cb-4d2b-9d9b-7c0f1cf9199a": "SB-TRN-007-Blue",
    "984e90c0-aa3a-4ac2-bcaa-1dec4e4a8cf5": "SB-GER-009-BLA-80G",
    "936dffda-b343-4820-801c-1a975f2255ee": "SB-GER-004-NAVB-60G",
    "b2d65ec5-4007-49c7-800a-5cb720d0abcf": "SB-TRN-009-BLA-L",
    "5923bcb8-30c8-45a9-a6cc-db83f0fb19a7": "SB-TRN-009-BLA-S",
    "551be94e-cd29-4826-9db7-0b20e576eea8": "SB-TRN-004-Blue",
    "753495f6-9266-4986-b5d6-7f48f80602ef": "SB-TRN-004-GRE",
    "45e16cd9-a4c6-43a7-a97f-ffb7eb6dd769": "SB-TRN-004-ORA",
}
PRODUCT_IDS = {
    uuid.UUID(s)
    for s in (
        "319d19b2-35f8-403d-bdb0-239e42350a51",
        "c95fb22b-d9c2-4a11-93c1-473566903a86",
        "1112c16f-4041-4142-a1c9-2891cdd7d19c",
        "5c69c04a-ebf4-425f-9e18-f7c4ba6c0147",
        "e1f6f48f-ef1e-47cb-8b82-462d79e5cf0b",
        "5d1b088c-582e-42d2-b835-30a0182485a7",
        "eb7e5b0b-59b5-4c6b-ab5e-531872464381",
        "5574b760-6942-4ea4-9b9b-5379b25dbddd",
    )
}
FATIMA_LINES = {
    uuid.UUID("4995f2bc-3dd2-403e-ba9b-0f1cc758fa5b"): uuid.UUID(
        "bbcdf0b7-e39a-4bab-90b1-9b65e1481b06"
    ),
    uuid.UUID("bca25a2b-9895-4911-a727-eef789e5737f"): uuid.UUID(
        "45e16cd9-a4c6-43a7-a97f-ffb7eb6dd769"
    ),
}
REASON = (
    "Owner approved Stocked sourcing and five units remaining per purchased "
    "variant on 24 November 2026; confirmed Fatima collected both items. "
    "Count correction only, acquisition costs unknown. Preserve original "
    "preorder snapshots and verified NGN 30000 payment."
)


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql), params)).mappings()]


async def audit(
    db: AsyncSession,
    *,
    entity: str,
    entity_id: uuid.UUID,
    action: str,
    actor: str,
    before: dict,
    after: dict,
    audit_id: uuid.UUID | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO store_audit_logs "
            "(id,domain,entity_type,entity_id,action,actor_label,old_value,new_value,reason,created_at) "
            "VALUES (:id,'store',:entity,:entity_id,:action,:actor,"
            "CAST(:before AS jsonb),CAST(:after AS jsonb),:reason,now())"
        ),
        {
            "id": audit_id or uuid.uuid4(),
            "entity": entity,
            "entity_id": entity_id,
            "action": action,
            "actor": actor,
            "before": json.dumps(before, default=str),
            "after": json.dumps({**after, "operation": OPERATION}, default=str),
            "reason": REASON,
        },
    )


async def prepare(
    db: AsyncSession, *, apply: bool = False, actor_label: str | None = None
) -> dict:
    """Prepare inventory atomically; caller commits only an explicit apply."""
    if apply and not actor_label:
        raise ValueError("Apply requires an honest --actor-label")
    if apply:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:op,0))"),
            {"op": OPERATION},
        )
    previous = await rows(
        db, "SELECT new_value FROM store_audit_logs WHERE id=:id", id=OPERATION_ID
    )
    if previous:
        return {**previous[0]["new_value"], "already_prepared": True}
    lock = " FOR UPDATE" if apply else ""
    orders = await rows(
        db,
        "SELECT id,order_number,status::text,fulfillment_type::text,total_ngn,"
        "payment_reference,fulfilled_at FROM store_orders WHERE id=:id" + lock,
        id=ORDER_ID,
    )
    if len(orders) != 1:
        raise ValueError("Reviewed Fatima order missing")
    order = orders[0]
    if (
        order["order_number"] != ORDER_NUMBER
        or order["status"] != "shipped"
        or order["fulfillment_type"] != "pickup"
        or order["total_ngn"] != Decimal("30000")
        or order["payment_reference"] != PAYMENT_REF
        or order["fulfilled_at"] is not None
    ):
        raise ValueError("Reviewed order identity, tender or handover state changed")
    payments = await rows(
        db,
        "SELECT status::text,amount,currency,purpose::text,entitlement_applied_at "
        "FROM payments WHERE reference=:ref" + (" FOR SHARE" if apply else ""),
        ref=PAYMENT_REF,
    )
    if (
        len(payments) != 1
        or any(
            payments[0][k] != v
            for k, v in {
                "status": "paid",
                "amount": 30000,
                "currency": "NGN",
                "purpose": "store_order",
            }.items()
        )
        or payments[0]["entitlement_applied_at"] is None
    ):
        raise ValueError("Existing NGN 30000 payment must be verified and applied")
    lines = await rows(
        db,
        "SELECT id,variant_id,quantity,is_preorder,unit_price_ngn,line_total_ngn "
        "FROM store_order_items WHERE order_id=:id ORDER BY id" + lock,
        id=ORDER_ID,
    )
    if {r["id"]: r["variant_id"] for r in lines} != FATIMA_LINES or any(
        r["quantity"] != 1
        or not r["is_preorder"]
        or r["unit_price_ngn"] != 15000
        or r["line_total_ngn"] != 15000
        for r in lines
    ):
        raise ValueError("Reviewed preorder lines changed")
    inventory = await rows(
        db,
        "SELECT v.id as variant_id,v.sku,p.id as product_id,p.sourcing_type::text,"
        "i.id as inventory_id,i.quantity_on_hand,i.quantity_reserved "
        "FROM store_product_variants v JOIN store_products p ON p.id=v.product_id "
        "JOIN store_inventory_items i ON i.variant_id=v.id "
        "WHERE v.id=ANY(:ids) ORDER BY v.id"
        + (" FOR UPDATE OF v,p,i" if apply else ""),
        ids=[uuid.UUID(v) for v in MANIFEST],
    )
    if {str(r["variant_id"]): r["sku"] for r in inventory} != MANIFEST:
        raise ValueError("Reviewed SKU manifest changed or inventory is missing")
    if {r["product_id"] for r in inventory} != PRODUCT_IDS:
        raise ValueError("Reviewed product families changed")
    if any(
        r["sourcing_type"] != "preorder"
        or r["quantity_on_hand"] != 0
        or r["quantity_reserved"] != 0
        for r in inventory
    ):
        raise ValueError(
            "Stock/sourcing changed since review; do not reset live counts"
        )
    values = await rows(
        db,
        "SELECT variant_id FROM store_inventory_valuations WHERE variant_id=ANY(:ids)",
        ids=[uuid.UUID(v) for v in MANIFEST],
    )
    if values:
        raise ValueError("Valued stock needs a separate cost reconciliation")
    result = {
        "operation": OPERATION,
        "products": len(PRODUCT_IDS),
        "variants": len(MANIFEST),
        "remaining_per_variant": REMAINING,
        "fatima_reserved_units": len(FATIMA_LINES),
        "acquisition_cost": "unknown",
        "payment_unchanged": True,
    }
    if not apply:
        return {**result, "dry_run": True}
    for product_id in sorted(PRODUCT_IDS):
        await db.execute(
            text(
                "UPDATE store_products SET sourcing_type='stocked',updated_at=now() WHERE id=:id"
            ),
            {"id": product_id},
        )
        await audit(
            db,
            entity="product",
            entity_id=product_id,
            action="store.sourcing_reconciled",
            actor=actor_label,
            before={"sourcing_type": "preorder"},
            after={"sourcing_type": "stocked"},
        )
    for item in inventory:
        reserved = int(item["variant_id"] in FATIMA_LINES.values())
        quantity = REMAINING + reserved
        await db.execute(
            text(
                "UPDATE store_inventory_items SET quantity_on_hand=:qty,quantity_reserved=:reserved,updated_at=now() WHERE id=:id"
            ),
            {"qty": quantity, "reserved": reserved, "id": item["inventory_id"]},
        )
        for kind, qty, reference in (
            ("adjustment", quantity, OPERATION_ID),
            ("reservation", reserved, ORDER_ID),
        ):
            if not qty:
                continue
            await db.execute(
                text(
                    "INSERT INTO store_inventory_movements (id,inventory_item_id,movement_type,quantity,reference_type,reference_id,notes,performed_by,created_at) VALUES (:id,:inventory,:kind,:qty,:ref_type,:ref,:reason,:actor,now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "inventory": item["inventory_id"],
                    "kind": kind,
                    "qty": qty,
                    "ref_type": "order"
                    if reserved and kind == "reservation"
                    else "count_correction",
                    "ref": reference,
                    "reason": REASON,
                    "actor": actor_label,
                },
            )
        await audit(
            db,
            entity="inventory",
            entity_id=item["inventory_id"],
            action="store.stock_count_reconciled",
            actor=actor_label,
            before={"on_hand": 0, "reserved": 0},
            after={
                "on_hand": quantity,
                "reserved": reserved,
                "remaining_after_fatima": REMAINING,
                "cost": "unknown",
            },
        )
    await audit(
        db,
        entity="order",
        entity_id=ORDER_ID,
        action="store.purchased_stock_prepared",
        actor=actor_label,
        before={"status": "shipped", "preorder_snapshot": True},
        after=result,
        audit_id=OPERATION_ID,
    )
    return {**result, "prepared": True}


async def complete() -> dict:
    """Use Store's existing authenticated API, preserving its completion pipeline."""
    from libs.common.service_client import internal_get, internal_patch

    kwargs = {
        "service_url": "http://127.0.0.1:8010",
        "calling_service": "store-stock-reconciliation",
        "timeout": 60.0,
    }
    path = f"/admin/store/orders/{ORDER_ID}"
    response = await internal_get(path=path, **kwargs)
    response.raise_for_status()
    status = response.json()["status"]
    if status == "picked_up":
        return {"status": status, "already_completed": True}
    if status != "shipped":
        raise ValueError("Order handover state changed; review before completing")
    response = await internal_patch(
        path=path + "/status",
        json={"status": "picked_up", "admin_notes": REASON},
        **kwargs,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Store completion returned HTTP {response.status_code}; verify saved state before retrying"
        )
    return {"status": response.json()["status"]}


async def run(args: argparse.Namespace) -> None:
    from libs.db.config import AsyncSessionLocal

    if args.complete and not args.apply:
        raise ValueError("--complete requires --apply")
    async with AsyncSessionLocal() as db:
        if not args.apply:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        result = await prepare(db, apply=args.apply, actor_label=args.actor_label)
        if args.apply:
            await db.commit()
        else:
            await db.rollback()
    print(json.dumps(result), flush=True)
    if args.complete:
        print(json.dumps(await complete()), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--actor-label")
    asyncio.run(run(parser.parse_args()))
