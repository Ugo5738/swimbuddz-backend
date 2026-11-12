"""Scoped preorder correction; dry-run by default, no cash settlement.

See docs/STORE_ORDER_CORRECTION_RUNBOOK.md. This maintenance transaction is not
runtime service integration. It preserves the frozen original receipt and holds
fulfillment until separately verified receipts cover the corrected order.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Mapping
from typing import Any
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

ORDER_ID = "c41194c9-e6a0-42b3-8623-11cc79f5a0bc"
AUTH_ID = "d7cc291f-a28d-47cc-a6bc-c6dc4e7b8585"
ITEM_ID = "2c6469e0-8792-49e1-bf9a-70b9ed737306"
OLD_VARIANT = "5bfe48c9-3766-4932-b009-2a9d1ca0a6e8"
NEW_VARIANT = "bbcdf0b7-e39a-4bab-90b1-9b65e1481b06"
ORIGINAL_REF = "store-order-c41194c9-e6a0-42b3-8623-11cc79f5a0bc-19e8ebd75677"
BANK_REF = "45ddcf4f-3dc1-4440-95f7-8913a8ac3d2e"
OPERATION = "esther-goggles-upgrade-20261112"
REASON = (
    "Owner requested black electroplated goggles with earplugs at NGN 15000 "
    "instead of NGN 10000 mirror goggles. Owner confirmed NGN 37000 and NGN "
    "5000 reached the bank on 2026-11-11; settlement still requires each "
    "actual bank reference. Preserve original evidence and prevent partial fulfillment."
)


async def _rows(
    db: AsyncSession, sql: str, params: dict[str, Any] | None = None
) -> list[RowMapping]:
    return list((await db.execute(text(sql), params or {})).mappings())


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    return row["metadata"] or {}


def _guard(topup_id: uuid.UUID) -> dict[str, Any]:
    return {
        "reason": "store_order_split_receipts_require_review",
        "operation": OPERATION,
        "order_id": ORDER_ID,
        "topup_payment_id": str(topup_id),
        "required_total_ngn": "42000.00",
    }


async def _audit(
    db: AsyncSession,
    actor: str,
    before: dict[str, Any],
    after: dict[str, Any],
    action: str,
) -> None:
    await db.execute(
        text(
            "INSERT INTO store_audit_logs "
            "(id,domain,entity_type,entity_id,action,actor_label,old_value,new_value,reason,created_at) "
            "VALUES (:id,'store','order',:order_id,:action,:actor, "
            "CAST(:before AS jsonb),CAST(:after AS jsonb),:reason,now())"
        ),
        {
            "id": uuid.uuid4(),
            "order_id": uuid.UUID(ORDER_ID),
            "action": action,
            "actor": actor,
            "before": json.dumps(before, default=str),
            "after": json.dumps(after, default=str),
            "reason": REASON,
        },
    )


async def correct(
    db: AsyncSession,
    *,
    apply: bool = False,
    release: bool = False,
    actor_label: str | None = None,
) -> dict[str, Any]:
    """Preview/stage the exact repair, or release only fully paid fulfillment."""
    if apply and not actor_label:
        raise ValueError("Apply requires an operator --actor-label")
    lock = " FOR UPDATE" if apply else ""
    orders = await _rows(
        db,
        "SELECT id,order_number,member_auth_id,status::text,subtotal_ngn,total_ngn, "
        "discount_amount_ngn,store_credit_applied_ngn,delivery_fee_ngn,bubbles_applied, "
        "wallet_hold_id,wallet_transaction_id,payment_reference,admin_notes "
        "FROM store_orders WHERE id=:id" + lock,
        {"id": uuid.UUID(ORDER_ID)},
    )
    if len(orders) != 1:
        raise ValueError("Reviewed order not found")
    order = orders[0]
    if (
        order["order_number"] != "SB-20261111-FATLF"
        or order["member_auth_id"] != AUTH_ID
        or order["payment_reference"] != ORIGINAL_REF
        or any(
            order[k]
            for k in [
                "discount_amount_ngn",
                "store_credit_applied_ngn",
                "delivery_fee_ngn",
                "bubbles_applied",
                "wallet_hold_id",
                "wallet_transaction_id",
            ]
        )
    ):
        raise ValueError("Order identity/tender changed; review again")
    items = await _rows(
        db,
        "SELECT id,variant_id,product_name,variant_name,sku,quantity,unit_price_ngn, "
        "line_total_ngn,is_preorder,supplier_id,supplier_name,estimated_ship_date "
        "FROM store_order_items WHERE order_id=:id ORDER BY id" + lock,
        {"id": uuid.UUID(ORDER_ID)},
    )
    if len(items) != 3 or any(
        i["quantity"] != 1 or not i["is_preorder"] for i in items
    ):
        raise ValueError("Reviewed three preorder items changed")
    item = next((i for i in items if str(i["id"]) == ITEM_ID), None)
    others = {i["sku"]: i["line_total_ngn"] for i in items if str(i["id"]) != ITEM_ID}
    if item is None or others != {
        "SB-GER-009-BLA-80G": Decimal("12000"),
        "SB-TRN-004-Blue": Decimal("15000"),
    }:
        raise ValueError("Reviewed line items changed")
    payments = await _rows(
        db,
        "SELECT id,reference,member_auth_id,purpose::text,amount,currency,status::text, "
        "payment_method,provider,provider_reference,paid_at,entitlement_applied_at, "
        "proof_of_payment_media_id,metadata FROM payments "
        "WHERE metadata->>'order_id'=:id OR reference=:ref ORDER BY id" + lock,
        {"id": ORDER_ID, "ref": ORIGINAL_REF},
    )
    primary = next((p for p in payments if p["reference"] == ORIGINAL_REF), None)
    if primary is None or primary["amount"] != 37000 or primary["currency"] != "NGN":
        raise ValueError("Original frozen payment changed")
    if any(
        p["member_auth_id"] != AUTH_ID
        or p["purpose"] != "store_order"
        or p["currency"] != "NGN"
        or _metadata(p).get("order_id") != ORDER_ID
        for p in payments
    ):
        raise ValueError("Unexpected receipt ownership, purpose or currency")
    saved = _metadata(primary).get("store_order_correction")
    if saved:
        topup = next(
            (p for p in payments if str(p["id"]) == saved.get("topup_payment_id")), None
        )
        if (
            saved.get("operation") != OPERATION
            or topup is None
            or len(payments) != 2
            or topup["amount"] != 5000
            or _metadata(topup).get("store_order_correction") != saved
            or str(item["variant_id"]) != NEW_VARIANT
            or item["sku"] != "SB-GER-001-BLA"
            or item["line_total_ngn"] != 15000
            or item["unit_price_ngn"] != 15000
            or order["subtotal_ngn"] != 42000
            or order["total_ngn"] != 42000
        ):
            raise ValueError("Saved correction no longer matches; review again")
        if release:
            return await _release(db, order, primary, topup, apply, actor_label)
        return {
            "operation": OPERATION,
            "already_applied": True,
            "total_ngn": "42000.00",
            "topup_reference": topup["reference"],
            "primary_status": primary["status"],
            "topup_status": topup["status"],
        }
    if release:
        raise ValueError("Stage the correction before releasing fulfillment")
    if (
        order["status"] != "pending_payment"
        or order["total_ngn"] != 37000
        or order["subtotal_ngn"] != 37000
        or len(payments) != 1
        or primary["status"] != "pending_review"
        or primary["paid_at"]
        or primary["entitlement_applied_at"]
        or not primary["proof_of_payment_media_id"]
        or primary["payment_method"] != "manual_transfer"
        or any(
            _metadata(primary).get(k)
            for k in [
                "checkout_reconciliation",
                "superseded_by_shared_receipt",
                "checkout_closed_unpaid",
                "recorded_offline",
            ]
        )
        or str(item["variant_id"]) != OLD_VARIANT
        or item["unit_price_ngn"] != 10000
        or item["line_total_ngn"] != 10000
    ):
        raise ValueError("Original unsettled order/receipt changed; review again")
    target = (
        await _rows(
            db,
            "SELECT v.id,v.sku,v.name,v.is_active,p.name AS product_name,p.sourcing_type::text, "
            "COALESCE(v.price_override_ngn,p.base_price_ngn) AS price,p.supplier_id, "
            "s.name AS supplier_name,p.preorder_lead_days FROM store_product_variants v "
            "JOIN store_products p ON p.id=v.product_id LEFT JOIN store_suppliers s ON s.id=p.supplier_id "
            "WHERE v.id=:id" + (" FOR SHARE OF v,p" if apply else ""),
            {"id": uuid.UUID(NEW_VARIANT)},
        )
    )[0]
    if (
        not target["is_active"]
        or target["sku"] != "SB-GER-001-BLA"
        or target["price"] != 15000
        or target["sourcing_type"] != "preorder"
    ):
        raise ValueError("Replacement catalogue quote changed")
    if apply:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "offline-receipt:" + BANK_REF},
        )
    duplicate = await _rows(
        db,
        "SELECT id FROM payments WHERE lower(provider_reference)=:ref "
        "OR lower(metadata->'submitted_transfer'->>'external_reference')=:ref "
        "OR lower(metadata->'admin_submitted_evidence'->>'external_reference')=:ref",
        {"ref": BANK_REF},
    )
    if duplicate:
        raise ValueError("Top-up bank reference already exists; do not duplicate it")
    topup_id, topup_ref = uuid.uuid4(), "PAY-" + uuid.uuid4().hex
    guard = _guard(topup_id)
    result = {
        "operation": OPERATION,
        "apply": apply,
        "old_sku": item["sku"],
        "new_sku": target["sku"],
        "old_total_ngn": "37000.00",
        "total_ngn": "42000.00",
        "original_payment_amount_ngn": "37000.00",
        "topup_amount_ngn": "5000.00",
        "topup_reference": topup_ref,
        "cash_settled": False,
        "fulfillment_held": True,
    }
    if not apply:
        return result
    primary_meta = {
        **_metadata(primary),
        "store_order_correction": guard,
        "checkout_reconciliation": guard,
    }
    topup_meta = {
        "order_id": ORDER_ID,
        "order_number": order["order_number"],
        "store_order_correction": guard,
        "checkout_reconciliation": guard,
        "submitted_transfer": {
            "external_reference": BANK_REF,
            "received_date": "2026-11-11",
            "note": "Owner supplied top-up reference and confirmed bank credit; requires separate Admin settlement.",
        },
        "admin_submitted_evidence": {"external_reference": BANK_REF, "note": REASON},
    }
    await db.execute(
        text(
            "INSERT INTO payments (id,reference,member_auth_id,payer_email,purpose,amount,currency, "
            "status,payment_method,admin_review_note,metadata,created_at,updated_at) "
            "SELECT :id,:ref,member_auth_id,payer_email,'store_order',5000,'NGN','pending_review', "
            "'manual_transfer',:note,CAST(:meta AS jsonb),now(),now() FROM payments WHERE id=:primary"
        ),
        {
            "id": topup_id,
            "ref": topup_ref,
            "note": REASON,
            "meta": json.dumps(topup_meta),
            "primary": primary["id"],
        },
    )
    await db.execute(
        text(
            "UPDATE payments SET metadata=CAST(:meta AS jsonb),entitlement_error=:note,updated_at=now() "
            "WHERE id=:id"
        ),
        {"meta": json.dumps(primary_meta), "note": REASON, "id": primary["id"]},
    )
    await db.execute(
        text(
            "UPDATE store_order_items SET variant_id=:variant,product_name=:product,variant_name=:name, "
            "sku=:sku,unit_price_ngn=15000,line_total_ngn=15000,supplier_id=:supplier, "
            "supplier_name=:supplier_name,estimated_ship_date=now()+make_interval(days=>:days) WHERE id=:id"
        ),
        {
            "id": uuid.UUID(ITEM_ID),
            "variant": uuid.UUID(NEW_VARIANT),
            "product": target["product_name"],
            "name": target["name"],
            "sku": target["sku"],
            "supplier": target["supplier_id"],
            "supplier_name": target["supplier_name"],
            "days": target["preorder_lead_days"] or 0,
        },
    )
    await db.execute(
        text(
            "UPDATE store_orders SET subtotal_ngn=42000,total_ngn=42000,admin_notes=:note,updated_at=now() "
            "WHERE id=:id"
        ),
        {
            "id": uuid.UUID(ORDER_ID),
            "note": (order["admin_notes"] or "")
            + "\n"
            + REASON
            + " Top-up checkout: "
            + topup_ref,
        },
    )
    await _audit(
        db,
        actor_label,
        {
            "item": dict(item),
            "total_ngn": "37000.00",
            "primary_reference": ORIGINAL_REF,
        },
        result,
        "store.preorder_item_corrected",
    )
    return result


async def _release(
    db: AsyncSession,
    order: RowMapping,
    primary: RowMapping,
    topup: RowMapping,
    apply: bool,
    actor: str | None,
) -> dict[str, Any]:
    saved = _metadata(primary)["store_order_correction"]
    refs = [(p["provider_reference"] or "").strip().lower() for p in [primary, topup]]
    if (
        any(
            p["status"] != "paid"
            or p["provider"] != "offline"
            or p["payment_method"] != "bank_transfer"
            or not p["paid_at"]
            for p in [primary, topup]
        )
        or not all(refs)
        or refs[0] == refs[1]
        or refs[1] != BANK_REF
        or sum(Decimal(str(p["amount"])) for p in [primary, topup])
        != order["total_ngn"]
    ):
        raise ValueError(
            "Both independent bank receipts must be verified PAID for the full NGN 42000"
        )
    meta = _metadata(primary)
    if meta.get("store_order_correction_released") == OPERATION:
        return {"already_released": True, "replay_reference": ORIGINAL_REF}
    if (
        order["status"] != "pending_payment"
        or meta.get("checkout_reconciliation") != saved
        or _metadata(topup).get("checkout_reconciliation") != saved
        or primary["entitlement_applied_at"]
        or topup["entitlement_applied_at"]
    ):
        raise ValueError("Fulfillment/hold changed; review before release")
    result = {
        "operation": OPERATION,
        "apply": apply,
        "replay_reference": ORIGINAL_REF,
        "verified_total_ngn": "42000.00",
        "posts_new_cash": False,
    }
    if apply:
        meta = {k: v for k, v in meta.items() if k != "checkout_reconciliation"}
        meta["store_order_correction_released"] = OPERATION
        await db.execute(
            text(
                "UPDATE payments SET metadata=CAST(:meta AS jsonb),entitlement_error=NULL,updated_at=now() "
                "WHERE id=:id"
            ),
            {"id": primary["id"], "meta": json.dumps(meta)},
        )
        await _audit(
            db, actor, {"held": True}, result, "store.preorder_fulfillment_released"
        )
    return result


async def run(args: argparse.Namespace) -> None:
    from libs.db.config import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        if not args.apply:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        result = await correct(
            db, apply=args.apply, release=args.release, actor_label=args.actor_label
        )
        if args.apply:
            await db.commit()
        else:
            await db.rollback()
        print(json.dumps(result, default=str), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--actor-label")
    asyncio.run(run(parser.parse_args()))
