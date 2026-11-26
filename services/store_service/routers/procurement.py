"""Store purchase order -> independent approval -> verified goods receipt.

Never pays suppliers, edits checkout payment status or fabricates inventory cost.
"""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from pydantic import BaseModel, Field
from services.store_service.models import (
    InventoryItem,
    InventoryMovement,
    InventoryMovementType,
    ProductVariant,
    Supplier,
)
from services.store_service.models.procurement import (
    StoreCostEvent,
    StoreGoodsReceipt,
    StoreGoodsReceiptLine,
    StoreInventoryValuation,
    StoreOpeningReconciliation,
    StoreProcurementGrant,
    StorePurchaseOrder,
    StorePurchaseOrderLine,
    StoreSupplierInvoice,
    StoreSupplierInvoiceSettlement,
    StoreTradeReceivable,
    StoreTradeReceivablePayment,
)
from services.store_service.routers.procurement_access import (
    procurement_owner,
    procurement_reader,
    procurement_writer,
    verify_ledger_journal_entry,
)
from services.store_service.services.ledger_sync import (
    post_credit_sale_approval_journal,
    post_credit_sale_payment_journal,
    post_goods_receipt_journal,
    post_opening_inventory_journal,
    post_return_cogs_reversal_journal,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/procurement", tags=["store-procurement"])


class GrantRequest(BaseModel):
    auth_id: str = Field(min_length=3, max_length=255)
    role: Literal["procurement_officer", "inventory_viewer"] = "procurement_officer"
    active: bool = True


class PurchaseLineRequest(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(gt=0, le=100000)
    landed_unit_cost_minor: int = Field(ge=0)


class PurchaseOrderRequest(BaseModel):
    supplier_id: uuid.UUID
    description: str = Field(min_length=5, max_length=1000)
    lines: list[PurchaseLineRequest] = Field(min_length=1, max_length=100)


class ReceiveLineRequest(BaseModel):
    purchase_line_id: uuid.UUID
    quantity: int = Field(gt=0)


class GoodsReceiptRequest(BaseModel):
    reference: str = Field(min_length=5, max_length=120)
    evidence_reference: str = Field(min_length=5, max_length=255)
    evidence_media_id: uuid.UUID | None = None
    lines: list[ReceiveLineRequest] = Field(min_length=1, max_length=100)


class SupplierInvoiceRequest(BaseModel):
    reference: str = Field(min_length=2, max_length=120)
    total_minor: int = Field(gt=0)
    due_on: date | None = None
    evidence_media_id: uuid.UUID | None = None


class SupplierInvoiceSettlementRequest(BaseModel):
    amount_minor: int = Field(gt=0)
    bank_reference: str = Field(min_length=3, max_length=180)
    treasury_request_id: uuid.UUID | None = None
    ledger_journal_entry_id: uuid.UUID | None = None
    evidence_media_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=1000)


class OpeningRequest(BaseModel):
    variant_id: uuid.UUID
    source_reference: str = Field(min_length=5, max_length=255)
    evidence_media_id: uuid.UUID | None = None
    quantity: int = Field(ge=0)
    total_cost_minor: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=1000)


class ReceivableRequest(BaseModel):
    order_id: uuid.UUID
    due_on: date
    reason: str = Field(min_length=10, max_length=1000)


class ReceivablePaymentRequest(BaseModel):
    amount_minor: int = Field(gt=0)
    bank_reference: str = Field(min_length=3, max_length=180)
    bank_account_ref: str = Field(
        default="bank_operating_ngn", min_length=2, max_length=80
    )
    evidence_media_id: uuid.UUID | None = None
    notes: str | None = Field(None, max_length=1000)


class ReturnLineRequest(BaseModel):
    order_item_id: uuid.UUID
    quantity: int = Field(gt=0)
    condition: Literal["restockable", "damaged"] = "restockable"


class OrderReturnRequest(BaseModel):
    reference: str = Field(min_length=3, max_length=120)
    reason: str = Field(min_length=5, max_length=1000)
    lines: list[ReturnLineRequest] = Field(min_length=1, max_length=100)


@router.put("/grants")
async def upsert_procurement_grant(
    payload: GrantRequest,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    if payload.auth_id == owner.auth_id and not payload.active:
        raise HTTPException(
            409, "Owner cannot revoke own current operational access here"
        )
    grant = await db.scalar(
        select(StoreProcurementGrant)
        .where(
            StoreProcurementGrant.auth_id == payload.auth_id,
        )
        .with_for_update()
    )
    if grant is None:
        grant = StoreProcurementGrant(
            auth_id=payload.auth_id, granted_by=owner.auth_id or str(owner.id)
        )
        db.add(grant)
    grant.role, grant.active = payload.role, payload.active
    await db.commit()
    return {"auth_id": grant.auth_id, "role": grant.role, "active": grant.active}


@router.get("/me")
async def procurement_me(
    actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    grant = await db.scalar(
        select(StoreProcurementGrant).where(
            StoreProcurementGrant.auth_id == actor.user_id,
            StoreProcurementGrant.active.is_(True),
        )
    )
    return {
        "role": grant.role if grant else "admin",
        "can_write": (
            actor.role == "service_role"
            or actor.has_role("admin")
            or (grant is not None and grant.role == "procurement_officer")
        ),
    }


@router.get("/orders")
async def list_purchase_orders(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    orders = (
        await db.scalars(
            select(StorePurchaseOrder)
            .order_by(
                StorePurchaseOrder.created_at.desc(),
            )
            .limit(150)
        )
    ).all()
    return [
        {
            "id": o.id,
            "order_number": o.order_number,
            "supplier_id": o.supplier_id,
            "status": o.status,
            "requested_by": o.requested_by,
            "approved_by": o.approved_by,
        }
        for o in orders
    ]


@router.get("/catalogue")
async def procurement_catalogue(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    suppliers = (
        await db.execute(
            select(Supplier.id, Supplier.name)
            .where(
                Supplier.is_active.is_(True),
            )
            .order_by(Supplier.name)
            .limit(150)
        )
    ).all()
    variants = (
        await db.execute(
            select(ProductVariant.id, ProductVariant.sku, ProductVariant.product_id)
            .where(
                ProductVariant.is_active.is_(True),
            )
            .order_by(ProductVariant.sku)
            .limit(1000)
        )
    ).all()
    return {
        "suppliers": [{"id": str(a), "name": b} for a, b in suppliers],
        "variants": [
            {"id": str(a), "sku": b, "product_id": str(c)} for a, b, c in variants
        ],
    }


@router.get("/orders/{order_id}")
async def get_purchase_order(
    order_id: uuid.UUID,
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    order = await db.scalar(
        select(StorePurchaseOrder).where(
            StorePurchaseOrder.id == order_id,
        )
    )
    if order is None:
        raise HTTPException(404, "Purchase order not found")
    rows = (
        await db.scalars(
            select(StorePurchaseOrderLine)
            .where(
                StorePurchaseOrderLine.order_id == order_id,
            )
            .order_by(StorePurchaseOrderLine.id)
        )
    ).all()
    return {
        "id": order.id,
        "supplier_id": order.supplier_id,
        "order_number": order.order_number,
        "status": order.status,
        "description": order.description,
        "requested_by": order.requested_by,
        "approved_by": order.approved_by,
        "lines": [
            {
                "id": line.id,
                "variant_id": line.variant_id,
                "quantity": line.quantity,
                "received": line.received,
                "unit_landed_minor": line.unit_landed_minor,
            }
            for line in rows
        ],
    }


@router.post("/orders", status_code=201)
async def create_purchase_order(
    payload: PurchaseOrderRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    supplier = await db.scalar(
        select(Supplier.id).where(
            Supplier.id == payload.supplier_id,
            Supplier.is_active.is_(True),
        )
    )
    if supplier is None:
        raise HTTPException(422, "Supplier is missing or inactive")
    ids = [line.variant_id for line in payload.lines]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Combine duplicate variants into one purchase line")
    known = set(
        (
            await db.scalars(
                select(ProductVariant.id).where(
                    ProductVariant.id.in_(ids),
                    ProductVariant.is_active.is_(True),
                )
            )
        ).all()
    )
    if known != set(ids):
        raise HTTPException(422, "Purchase contains unknown or inactive variants")
    order_id = uuid.uuid4()
    number = "PO-" + order_id.hex[:16].upper()
    db.add(
        StorePurchaseOrder(
            id=order_id,
            supplier_id=payload.supplier_id,
            order_number=number,
            description=payload.description,
            requested_by=actor.user_id,
        )
    )
    for line in payload.lines:
        db.add(
            StorePurchaseOrderLine(
                order_id=order_id,
                variant_id=line.variant_id,
                quantity=line.quantity,
                unit_landed_minor=line.landed_unit_cost_minor,
            )
        )
    await db.commit()
    return {"id": order_id, "number": number, "status": "draft"}


@router.post("/orders/{order_id}/submit")
async def submit_purchase_order(
    order_id: uuid.UUID,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(
        select(StorePurchaseOrder)
        .where(
            StorePurchaseOrder.id == order_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "Purchase order not found")
    if item.status != "draft" or item.requested_by != actor.user_id:
        raise HTTPException(409, "Only creator can submit a draft")
    item.status, item.submitted_at = "submitted", utc_now()
    await db.commit()
    return {"id": item.id, "status": item.status}


@router.post("/orders/{order_id}/decision")
async def decide_purchase_order(
    order_id: uuid.UUID,
    approve: bool,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(
        select(StorePurchaseOrder)
        .where(
            StorePurchaseOrder.id == order_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "Purchase order not found")
    if item.status != "submitted":
        raise HTTPException(409, "Order must be submitted before independent decision")
    if owner.auth_id == item.requested_by:
        raise HTTPException(
            403, "The purchase requester cannot approve their own request"
        )
    item.status = "approved" if approve else "rejected"
    item.approved_by = owner.auth_id or str(owner.id)
    item.approved_at = utc_now()
    await db.commit()
    return {"id": item.id, "status": item.status, "approved_by": item.approved_by}


@router.post("/orders/{order_id}/receive", status_code=201)
async def receive_goods(
    order_id: uuid.UUID,
    payload: GoodsReceiptRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(
        select(StorePurchaseOrder)
        .where(
            StorePurchaseOrder.id == order_id,
        )
        .with_for_update()
    )
    if item is None or item.status not in ("approved", "partially_received"):
        raise HTTPException(409, "Receive goods only against an approved purchase")
    prior = await db.scalar(
        select(StoreGoodsReceipt.id).where(
            StoreGoodsReceipt.reference == payload.reference,
        )
    )
    if prior:
        raise HTTPException(409, "Goods receipt reference already recorded")
    incoming_ids = [line.purchase_line_id for line in payload.lines]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise HTTPException(422, "Repeated line in receipt")
    lines = (
        await db.scalars(
            select(StorePurchaseOrderLine)
            .where(
                StorePurchaseOrderLine.order_id == order_id,
            )
            .order_by(StorePurchaseOrderLine.id)
            .with_for_update()
        )
    ).all()
    lookup = {line.id: line for line in lines}
    if not set(incoming_ids).issubset(lookup):
        raise HTTPException(422, "Receipt includes a line from a different order")
    receipt_id = uuid.uuid4()
    receipt = StoreGoodsReceipt(
        id=receipt_id,
        order_id=order_id,
        reference=payload.reference,
        evidence_reference=payload.evidence_reference,
        evidence_media_id=payload.evidence_media_id,
        actor_auth_id=actor.user_id,
    )
    db.add(receipt)
    receipt_total_cost_minor = 0
    for line in payload.lines:
        po_line = lookup[line.purchase_line_id]
        if po_line.received + line.quantity > po_line.quantity:
            raise HTTPException(
                409, "Received quantity exceeds approved purchase order"
            )
        inventory = await db.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.variant_id == po_line.variant_id,
            )
            .with_for_update()
        )
        if inventory is None:
            inventory = InventoryItem(
                variant_id=po_line.variant_id, quantity_on_hand=0, quantity_reserved=0
            )
            db.add(inventory)
            await db.flush()
        valuation = await db.scalar(
            select(StoreInventoryValuation)
            .where(
                StoreInventoryValuation.variant_id == po_line.variant_id,
            )
            .with_for_update()
        )
        if valuation is None:
            valuation = StoreInventoryValuation(
                variant_id=po_line.variant_id, quantity=0, total_cost_minor=0
            )
            db.add(valuation)
            await db.flush()
        if valuation.quantity != inventory.quantity_on_hand:
            raise HTTPException(
                409,
                "Existing stock must have an approved valuation before receiving new goods",
            )
        cost = line.quantity * po_line.unit_landed_minor
        receipt_total_cost_minor += cost
        inventory.quantity_on_hand += line.quantity
        inventory.last_restock_at = utc_now()
        valuation.quantity += line.quantity
        valuation.total_cost_minor += cost
        po_line.received += line.quantity
        db.add(
            StoreGoodsReceiptLine(
                receipt_id=receipt_id,
                purchase_line_id=po_line.id,
                quantity=line.quantity,
                unit_landed_minor=po_line.unit_landed_minor,
            )
        )
        db.add(
            StoreCostEvent(
                variant_id=po_line.variant_id,
                event_kind="receipt",
                quantity_delta=line.quantity,
                cost_delta_minor=cost,
                reference=f"receipt:{receipt_id}:{po_line.id}",
                evidence=payload.evidence_reference,
                actor_auth_id=actor.user_id,
            )
        )
        db.add(
            InventoryMovement(
                inventory_item_id=inventory.id,
                movement_type=InventoryMovementType.RESTOCK,
                quantity=line.quantity,
                reference_type="goods_receipt",
                reference_id=receipt_id,
                notes=payload.evidence_reference,
                performed_by=actor.user_id,
            )
        )
    item.status = (
        "received"
        if all(item_line.received == item_line.quantity for item_line in lines)
        else "partially_received"
    )
    await db.commit()

    entry_id, sync_status = await post_goods_receipt_journal(
        receipt_id=receipt_id,
        order_number=item.order_number,
        entry_date=date.today(),
        total_cost_minor=receipt_total_cost_minor,
        evidence_reference=payload.evidence_reference,
    )
    receipt.ledger_entry_id = entry_id
    receipt.ledger_sync_status = sync_status
    await db.commit()
    return {
        "id": receipt_id,
        "order_id": order_id,
        "status": item.status,
        "total_cost_minor": receipt_total_cost_minor,
        "ledger_entry_id": entry_id,
        "ledger_sync_status": sync_status,
    }


@router.post("/receipts/{receipt_id}/sync-ledger")
async def retry_goods_receipt_ledger_sync(
    receipt_id: uuid.UUID,
    _owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    """Idempotently replay Ledger posting for a goods receipt with pending_retry."""
    receipt = await db.scalar(
        select(StoreGoodsReceipt)
        .where(StoreGoodsReceipt.id == receipt_id)
        .with_for_update()
    )
    if receipt is None:
        raise HTTPException(404, "Goods receipt not found")
    if receipt.ledger_sync_status == "posted" and receipt.ledger_entry_id:
        return {
            "id": receipt.id,
            "ledger_entry_id": receipt.ledger_entry_id,
            "ledger_sync_status": receipt.ledger_sync_status,
        }
    po = await db.scalar(
        select(StorePurchaseOrder).where(StorePurchaseOrder.id == receipt.order_id)
    )
    total_minor = int(
        await db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StoreGoodsReceiptLine.quantity
                        * StoreGoodsReceiptLine.unit_landed_minor
                    ),
                    0,
                )
            ).where(StoreGoodsReceiptLine.receipt_id == receipt.id)
        )
        or 0
    )
    entry_id, sync_status = await post_goods_receipt_journal(
        receipt_id=receipt.id,
        order_number=po.order_number if po else str(receipt.order_id),
        entry_date=receipt.received_at.date(),
        total_cost_minor=total_minor,
        evidence_reference=receipt.evidence_reference,
    )
    receipt.ledger_entry_id = entry_id
    receipt.ledger_sync_status = sync_status
    await db.commit()
    return {
        "id": receipt.id,
        "ledger_entry_id": entry_id,
        "ledger_sync_status": sync_status,
    }


@router.post("/orders/{order_id}/invoices", status_code=201)
async def record_supplier_invoice(
    order_id: uuid.UUID,
    payload: SupplierInvoiceRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(
        select(StorePurchaseOrder).where(StorePurchaseOrder.id == order_id)
    )
    if not item or item.status not in ("approved", "partially_received", "received"):
        raise HTTPException(409, "Supplier invoice requires an approved order")
    # Lock the purchase first: an invoice is a liability against an already
    # approved commercial ceiling, not an unrestricted new spending authority.
    await db.scalar(
        select(StorePurchaseOrder.id)
        .where(
            StorePurchaseOrder.id == order_id,
        )
        .with_for_update()
    )
    approved_minor = int(
        await db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        StorePurchaseOrderLine.quantity
                        * StorePurchaseOrderLine.unit_landed_minor,
                    ),
                    0,
                ),
            ).where(StorePurchaseOrderLine.order_id == order_id)
        )
        or 0
    )
    invoiced_minor = int(
        await db.scalar(
            select(
                func.coalesce(func.sum(StoreSupplierInvoice.total_minor), 0),
            ).where(StoreSupplierInvoice.order_id == order_id)
        )
        or 0
    )
    if invoiced_minor + payload.total_minor > approved_minor:
        raise HTTPException(409, "Invoices exceed approved purchase order amount")
    invoice = StoreSupplierInvoice(
        supplier_id=item.supplier_id,
        order_id=order_id,
        reference=payload.reference,
        total_minor=payload.total_minor,
        due_on=payload.due_on,
        evidence_media_id=payload.evidence_media_id,
        created_by=actor.user_id,
    )
    db.add(invoice)
    await db.commit()
    return {
        "id": invoice.id,
        "total_minor": invoice.total_minor,
        "verified_paid_minor": 0,
        "evidence_media_id": invoice.evidence_media_id,
    }


@router.get("/liabilities")
async def supplier_liabilities(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.scalars(
            select(StoreSupplierInvoice)
            .order_by(
                StoreSupplierInvoice.id,
            )
            .limit(300)
        )
    ).all()
    return [
        {
            "id": row.id,
            "supplier_id": row.supplier_id,
            "order_id": row.order_id,
            "reference": row.reference,
            "due_on": row.due_on,
            "total_minor": row.total_minor,
            "verified_paid_minor": row.verified_paid_minor,
            "outstanding_minor": row.total_minor - row.verified_paid_minor,
            "status": (
                "paid"
                if row.verified_paid_minor >= row.total_minor
                else "partially_paid"
                if row.verified_paid_minor > 0
                else "unpaid"
            ),
            "evidence_media_id": row.evidence_media_id,
        }
        for row in rows
    ]


@router.get("/invoices/{invoice_id}/settlements")
async def list_supplier_invoice_settlements(
    invoice_id: uuid.UUID,
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.scalars(
            select(StoreSupplierInvoiceSettlement)
            .where(StoreSupplierInvoiceSettlement.invoice_id == invoice_id)
            .order_by(StoreSupplierInvoiceSettlement.settled_at.desc())
        )
    ).all()
    return [
        {
            "id": r.id,
            "invoice_id": r.invoice_id,
            "amount_minor": r.amount_minor,
            "bank_reference": r.bank_reference,
            "treasury_request_id": r.treasury_request_id,
            "ledger_journal_entry_id": r.ledger_journal_entry_id,
            "evidence_media_id": r.evidence_media_id,
            "settled_by": r.settled_by,
            "notes": r.notes,
            "settled_at": r.settled_at,
        }
        for r in rows
    ]


@router.post("/invoices/{invoice_id}/settlements", status_code=201)
async def settle_supplier_invoice(
    invoice_id: uuid.UUID,
    payload: SupplierInvoiceSettlementRequest,
    request: Request,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    """Record Finance Owner verified settlement against a supplier invoice.

    Supports partial and full payments, blocks overpayment, enforces separation
    from the invoice preparer, and verifies any linked Ledger journal entry via
    Ledger HTTP API (typically from a confirmed Treasury ``liability_settlement``).
    """
    invoice = await db.scalar(
        select(StoreSupplierInvoice)
        .where(StoreSupplierInvoice.id == invoice_id)
        .with_for_update()
    )
    if invoice is None:
        raise HTTPException(404, "Supplier invoice not found")
    if owner.auth_id == invoice.created_by:
        raise HTTPException(
            403, "Invoice preparer cannot independently certify their own settlement"
        )
    outstanding = invoice.total_minor - invoice.verified_paid_minor
    if payload.amount_minor > outstanding:
        raise HTTPException(
            409,
            f"Settlement exceeds unpaid invoice balance ({outstanding} minor units remaining)",
        )
    duplicate = await db.scalar(
        select(StoreSupplierInvoiceSettlement.id).where(
            StoreSupplierInvoiceSettlement.invoice_id == invoice_id,
            StoreSupplierInvoiceSettlement.bank_reference
            == payload.bank_reference.strip(),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            409,
            "A settlement with this bank reference is already recorded on this invoice",
        )
    if payload.ledger_journal_entry_id is not None:
        await verify_ledger_journal_entry(request, payload.ledger_journal_entry_id)

    settlement = StoreSupplierInvoiceSettlement(
        invoice_id=invoice.id,
        amount_minor=payload.amount_minor,
        bank_reference=payload.bank_reference.strip(),
        treasury_request_id=payload.treasury_request_id,
        ledger_journal_entry_id=payload.ledger_journal_entry_id,
        evidence_media_id=payload.evidence_media_id,
        settled_by=owner.auth_id or str(owner.id),
        notes=(payload.notes or "").strip() or None,
    )
    db.add(settlement)
    invoice.verified_paid_minor += payload.amount_minor
    await db.commit()
    return {
        "id": settlement.id,
        "invoice_id": invoice.id,
        "amount_minor": settlement.amount_minor,
        "verified_paid_minor": invoice.verified_paid_minor,
        "outstanding_minor": invoice.total_minor - invoice.verified_paid_minor,
        "ledger_journal_entry_id": settlement.ledger_journal_entry_id,
        "evidence_media_id": settlement.evidence_media_id,
    }


@router.get("/inventory-values")
async def procurement_inventory_values(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.execute(
            select(
                InventoryItem.variant_id,
                InventoryItem.quantity_on_hand,
                StoreInventoryValuation.quantity,
                StoreInventoryValuation.total_cost_minor,
            )
            .outerjoin(
                StoreInventoryValuation,
                StoreInventoryValuation.variant_id == InventoryItem.variant_id,
            )
            .limit(400)
        )
    ).all()
    return [
        {
            "variant_id": variant_id,
            "on_hand": on_hand,
            "valued_quantity": cost_quantity,
            "total_cost_minor": total_cost,
            "reconciled": cost_quantity == on_hand,
        }
        for variant_id, on_hand, cost_quantity, total_cost in rows
    ]


@router.get("/openings")
async def list_opening_reconciliations(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.scalars(
            select(StoreOpeningReconciliation)
            .order_by(
                StoreOpeningReconciliation.created_at.desc(),
            )
            .limit(200)
        )
    ).all()
    return [
        {
            "id": row.id,
            "variant_id": row.variant_id,
            "source_reference": row.source_reference,
            "quantity": row.quantity,
            "total_cost_minor": row.total_cost_minor,
            "status": row.status,
            "proposed_by": row.proposed_by,
            "approved_by": row.approved_by,
            "evidence_media_id": row.evidence_media_id,
            "ledger_entry_id": row.ledger_entry_id,
            "ledger_sync_status": row.ledger_sync_status,
        }
        for row in rows
    ]


@router.post("/openings", status_code=201)
async def propose_opening_valuation(
    payload: OpeningRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    if payload.quantity and not payload.total_cost_minor:
        raise HTTPException(
            422, "Historical stock must have documented nonzero acquisition cost"
        )
    item = await db.scalar(
        select(InventoryItem).where(
            InventoryItem.variant_id == payload.variant_id,
        )
    )
    if item is None or item.quantity_on_hand != payload.quantity:
        raise HTTPException(
            409,
            "Physical count must match existing stock; review adjustments separately",
        )
    proposed = StoreOpeningReconciliation(
        variant_id=payload.variant_id,
        source_reference=payload.source_reference,
        evidence_media_id=payload.evidence_media_id,
        quantity=payload.quantity,
        total_cost_minor=payload.total_cost_minor,
        reason=payload.reason,
        proposed_by=actor.user_id,
    )
    db.add(proposed)
    await db.commit()
    return {"id": proposed.id, "status": proposed.status}


@router.post("/openings/{opening_id}/decision")
async def approve_opening_valuation(
    opening_id: uuid.UUID,
    approve: bool,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(
        select(StoreOpeningReconciliation)
        .where(
            StoreOpeningReconciliation.id == opening_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "Opening reconciliation missing")
    if item.status != "pending":
        raise HTTPException(409, "Already reviewed")
    if item.proposed_by == owner.auth_id:
        raise HTTPException(403, "Cannot independently approve your own opening value")
    if approve:
        inventory = await db.scalar(
            select(InventoryItem)
            .where(
                InventoryItem.variant_id == item.variant_id,
            )
            .with_for_update()
        )
        if not inventory or inventory.quantity_on_hand != item.quantity:
            raise HTTPException(
                409, "Stock changed since physical count; recount first"
            )
        valuation = await db.scalar(
            select(StoreInventoryValuation)
            .where(
                StoreInventoryValuation.variant_id == item.variant_id,
            )
            .with_for_update()
        )
        if valuation is not None and (valuation.quantity or valuation.total_cost_minor):
            raise HTTPException(
                409, "Existing valuation is not empty; do not overwrite"
            )
        if valuation is None:
            valuation = StoreInventoryValuation(variant_id=item.variant_id)
            db.add(valuation)
        valuation.quantity = item.quantity
        valuation.total_cost_minor = item.total_cost_minor
        db.add(
            StoreCostEvent(
                variant_id=item.variant_id,
                event_kind="opening",
                quantity_delta=item.quantity,
                cost_delta_minor=item.total_cost_minor,
                reference=f"opening:{item.id}",
                evidence=item.source_reference,
                actor_auth_id=owner.auth_id or str(owner.id),
            )
        )
    item.status = "approved" if approve else "rejected"
    item.approved_by = owner.auth_id or str(owner.id)
    await db.commit()

    if approve and item.total_cost_minor > 0:
        entry_id, sync_status = await post_opening_inventory_journal(
            opening_id=item.id,
            variant_id=item.variant_id,
            entry_date=date.today(),
            total_cost_minor=item.total_cost_minor,
            source_reference=item.source_reference,
        )
        item.ledger_entry_id = entry_id
        item.ledger_sync_status = sync_status
        await db.commit()

    return {
        "id": item.id,
        "status": item.status,
        "ledger_entry_id": item.ledger_entry_id,
        "ledger_sync_status": item.ledger_sync_status,
    }


@router.get("/unpaid-orders")
async def eligible_credit_orders(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    from services.store_service.models import Order, OrderStatus

    orders = (
        await db.scalars(
            select(Order)
            .where(
                Order.status == OrderStatus.PENDING_PAYMENT,
                Order.paid_at.is_(None),
            )
            .order_by(Order.created_at.desc())
            .limit(100)
        )
    ).all()
    return [
        {
            "id": o.id,
            "order_number": o.order_number,
            "total_minor": int(o.total_ngn * 100),
        }
        for o in orders
    ]


@router.post("/receivables", status_code=201)
async def propose_trade_receivable(
    payload: ReceivableRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    from services.store_service.models import Order, OrderStatus

    order = await db.scalar(select(Order).where(Order.id == payload.order_id))
    if not order:
        raise HTTPException(404, "Store order not found")
    if order.status != OrderStatus.PENDING_PAYMENT or order.paid_at is not None:
        raise HTTPException(
            409, "Credit terms may only be requested for unpaid open orders"
        )
    if payload.due_on < date.today():
        raise HTTPException(422, "Credit due date cannot be in the past")
    from decimal import Decimal

    minor = int(Decimal(str(order.total_ngn)) * 100)
    if minor <= 0:
        raise HTTPException(422, "Credit amount must be positive")
    row = StoreTradeReceivable(
        order_id=order.id,
        amount_minor=minor,
        due_on=payload.due_on,
        reason=payload.reason,
        requested_by=actor.user_id,
    )
    db.add(row)
    await db.commit()
    return {"id": row.id, "status": row.status, "outstanding_minor": minor}


@router.post("/receivables/{receivable_id}/decision")
async def decide_trade_receivable(
    receivable_id: uuid.UUID,
    approve: bool,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    from services.store_service.models import Order, OrderStatus

    row = await db.scalar(
        select(StoreTradeReceivable)
        .where(
            StoreTradeReceivable.id == receivable_id,
        )
        .with_for_update()
    )
    if row is None or row.status != "requested":
        raise HTTPException(409, "Credit request must be pending")
    order = await db.scalar(select(Order).where(Order.id == row.order_id))
    if (
        not order
        or order.paid_at is not None
        or order.status != OrderStatus.PENDING_PAYMENT
    ):
        raise HTTPException(409, "This order is no longer unpaid and open")
    if owner.auth_id in (row.requested_by, order.member_auth_id):
        raise HTTPException(403, "Cannot approve your own requested or personal credit")
    row.status = "approved" if approve else "rejected"
    row.approved_by = owner.auth_id or str(owner.id)
    if approve:
        row.approved_at = utc_now()
    # Per ADR-023: independent credit approval unlocks controlled fulfillment
    # and books Accounts Receivable immediately, while keeping OrderStatus.PAID
    # strictly gated on full verified settlement.
    await db.commit()

    if approve:
        entry_id, sync_status = await post_credit_sale_approval_journal(
            receivable_id=row.id,
            order_id=order.id,
            order_number=order.order_number,
            entry_date=date.today(),
            amount_minor=row.amount_minor,
            member_auth_id=order.member_auth_id,
        )
        row.ledger_entry_id = entry_id
        row.ledger_sync_status = sync_status
        await db.commit()

    return {
        "id": row.id,
        "status": row.status,
        "can_fulfill": row.status in ("approved", "settled"),
        "ledger_entry_id": row.ledger_entry_id,
        "ledger_sync_status": row.ledger_sync_status,
    }


@router.get("/receivables/{receivable_id}/payments")
async def list_receivable_payments(
    receivable_id: uuid.UUID,
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.scalars(
            select(StoreTradeReceivablePayment)
            .where(StoreTradeReceivablePayment.receivable_id == receivable_id)
            .order_by(StoreTradeReceivablePayment.paid_at.desc())
        )
    ).all()
    return [
        {
            "id": r.id,
            "receivable_id": r.receivable_id,
            "amount_minor": r.amount_minor,
            "bank_reference": r.bank_reference,
            "bank_account_ref": r.bank_account_ref,
            "evidence_media_id": r.evidence_media_id,
            "ledger_entry_id": r.ledger_entry_id,
            "ledger_sync_status": r.ledger_sync_status,
            "verified_by": r.verified_by,
            "notes": r.notes,
            "paid_at": r.paid_at,
        }
        for r in rows
    ]


@router.post("/receivables/{receivable_id}/payments", status_code=201)
async def record_receivable_payment(
    receivable_id: uuid.UUID,
    payload: ReceivablePaymentRequest,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    """Record a Finance Owner verified partial or full repayment on approved credit.

    Enforces separation from the customer, prevents overpayment, posts
    DR <bank> / CR accounts_receivable to Ledger, and marks the receivable
    ``settled`` + stamps ``order.paid_at`` once fully paid.
    """
    from services.store_service.models import Order, OrderStatus

    row = await db.scalar(
        select(StoreTradeReceivable)
        .where(StoreTradeReceivable.id == receivable_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Credit receivable not found")
    if row.status != "approved":
        raise HTTPException(
            409, "Repayments may only be recorded on approved open credit receivables"
        )
    order = await db.scalar(
        select(Order).where(Order.id == row.order_id).with_for_update()
    )
    if order is None:
        raise HTTPException(404, "Store order not found")
    if owner.auth_id == order.member_auth_id:
        raise HTTPException(
            403, "Buyer cannot independently verify payment on their own credit order"
        )
    outstanding = row.amount_minor - row.verified_paid_minor
    if payload.amount_minor > outstanding:
        raise HTTPException(
            409,
            f"Repayment exceeds remaining credit balance ({outstanding} minor units remaining)",
        )
    clean_ref = payload.bank_reference.strip()
    existing_ref = await db.scalar(
        select(StoreTradeReceivablePayment.id).where(
            StoreTradeReceivablePayment.bank_reference == clean_ref
        )
    )
    if existing_ref is not None:
        raise HTTPException(
            409, "Bank reference already recorded against a credit repayment"
        )

    payment = StoreTradeReceivablePayment(
        receivable_id=row.id,
        amount_minor=payload.amount_minor,
        bank_reference=clean_ref,
        bank_account_ref=payload.bank_account_ref.strip(),
        evidence_media_id=payload.evidence_media_id,
        verified_by=owner.auth_id or str(owner.id),
        notes=(payload.notes or "").strip() or None,
    )
    db.add(payment)
    row.verified_paid_minor += payload.amount_minor
    fully_settled = row.verified_paid_minor >= row.amount_minor
    if fully_settled:
        now = utc_now()
        row.status = "settled"
        row.settled_at = now
        if order.paid_at is None:
            order.paid_at = now
        if order.status == OrderStatus.PENDING_PAYMENT:
            order.status = OrderStatus.PAID
    await db.commit()

    entry_id, sync_status = await post_credit_sale_payment_journal(
        payment_id=payment.id,
        receivable_id=row.id,
        order_number=order.order_number,
        entry_date=date.today(),
        amount_minor=payment.amount_minor,
        bank_account_ref=payment.bank_account_ref,
        bank_reference=payment.bank_reference,
        member_auth_id=order.member_auth_id,
    )
    payment.ledger_entry_id = entry_id
    payment.ledger_sync_status = sync_status
    await db.commit()

    return {
        "id": payment.id,
        "receivable_id": row.id,
        "status": row.status,
        "amount_minor": payment.amount_minor,
        "verified_paid_minor": row.verified_paid_minor,
        "outstanding_minor": row.amount_minor - row.verified_paid_minor,
        "order_status": order.status.value,
        "ledger_entry_id": entry_id,
        "ledger_sync_status": sync_status,
    }


@router.get("/receivables")
async def list_receivables(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    from services.store_service.models import Order

    rows = (
        await db.execute(
            select(StoreTradeReceivable, Order.order_number, Order.status)
            .join(Order, Order.id == StoreTradeReceivable.order_id)
            .order_by(StoreTradeReceivable.id)
            .limit(250)
        )
    ).all()
    return [
        {
            "id": r.id,
            "order_id": r.order_id,
            "order_number": order_number,
            "order_status": order_status.value
            if hasattr(order_status, "value")
            else str(order_status),
            "status": r.status,
            "due_on": r.due_on,
            "amount_minor": r.amount_minor,
            "verified_paid_minor": r.verified_paid_minor,
            "outstanding_minor": r.amount_minor - r.verified_paid_minor,
            "can_fulfill": r.status in ("approved", "settled"),
            "ledger_entry_id": r.ledger_entry_id,
            "ledger_sync_status": r.ledger_sync_status,
        }
        for r, order_number, order_status in rows
    ]


@router.get("/fulfilled-orders")
async def list_fulfilled_orders_for_return(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    """List recently fulfilled orders with their line items for audited return/COGS reversal."""
    from services.store_service.models import Order, OrderItem, OrderStatus

    orders = (
        await db.scalars(
            select(Order)
            .where(
                Order.status.in_(
                    [OrderStatus.PICKED_UP, OrderStatus.DELIVERED, OrderStatus.REFUNDED]
                )
            )
            .order_by(Order.created_at.desc())
            .limit(80)
        )
    ).all()
    order_ids = [o.id for o in orders]
    items_by_order: dict[uuid.UUID, list[dict]] = {oid: [] for oid in order_ids}
    if order_ids:
        items = (
            await db.scalars(select(OrderItem).where(OrderItem.order_id.in_(order_ids)))
        ).all()
        for it in items:
            items_by_order[it.order_id].append(
                {
                    "id": it.id,
                    "variant_id": it.variant_id,
                    "product_name": it.product_name,
                    "variant_name": it.variant_name,
                    "quantity": it.quantity,
                }
            )
    return [
        {
            "id": o.id,
            "order_number": o.order_number,
            "status": o.status.value,
            "total_minor": int(o.total_ngn * 100),
            "items": items_by_order.get(o.id, []),
        }
        for o in orders
    ]


@router.post("/orders/{order_id}/returns", status_code=201)
async def record_fulfilled_order_return(
    order_id: uuid.UUID,
    payload: OrderReturnRequest,
    owner=Depends(procurement_owner),
    db: AsyncSession = Depends(get_async_db),
):
    """Audited fulfilled-order return restoring restockable inventory & reversing COGS.

    Looks up the exact historical ``sale`` cost event per line, prevents returning
    more than originally sold, restores ``InventoryItem`` + ``StoreInventoryValuation``
    and posts ``DR store_inventory / CR cogs_store`` for ``restockable`` items, or
    records a ``return_damaged`` shrinkage audit event for damaged goods.
    """
    from services.store_service.models import Order, OrderItem, OrderStatus

    order = await db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(404, "Store order not found")
    if order.status not in (
        OrderStatus.PICKED_UP,
        OrderStatus.DELIVERED,
        OrderStatus.REFUNDED,
    ):
        raise HTTPException(
            409,
            "Inventory/COGS return reversal applies only to fulfilled orders",
        )
    if owner.auth_id == order.member_auth_id:
        raise HTTPException(
            403,
            "Buyer cannot independently process a return/COGS reversal on their own order",
        )
    line_ids = [line.order_item_id for line in payload.lines]
    if len(line_ids) != len(set(line_ids)):
        raise HTTPException(
            422, "Combine duplicate order items in a single return line"
        )

    order_items = (
        await db.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
    ).all()
    item_map = {it.id: it for it in order_items}
    if not set(line_ids).issubset(item_map):
        raise HTTPException(422, "Return contains an item not belonging to this order")

    clean_ref = payload.reference.strip()
    total_restored_cost_minor = 0
    results = []

    for req_line in payload.lines:
        order_item = item_map[req_line.order_item_id]
        event_ref = f"return:{clean_ref}:{order_item.id}"
        dup = await db.scalar(
            select(StoreCostEvent.id).where(StoreCostEvent.reference == event_ref)
        )
        if dup is not None:
            raise HTTPException(409, f"Return reference {clean_ref!r} already recorded")

        prior_returned = int(
            await db.scalar(
                select(
                    func.coalesce(func.sum(func.abs(StoreCostEvent.quantity_delta)), 0)
                ).where(
                    StoreCostEvent.variant_id == order_item.variant_id,
                    StoreCostEvent.evidence
                    == f"return-order:{order.id}:{order_item.id}",
                )
            )
            or 0
        )
        if prior_returned + req_line.quantity > order_item.quantity:
            raise HTTPException(
                409,
                f"Return quantity exceeds remaining returnable units ({order_item.quantity - prior_returned} left)",
            )

        sale_event = await db.scalar(
            select(StoreCostEvent).where(
                StoreCostEvent.reference == f"sale:{order_item.id}",
                StoreCostEvent.event_kind == "sale",
            )
        )
        restored_cost_minor = 0
        if sale_event is not None and abs(sale_event.quantity_delta) > 0:
            sold_qty = abs(sale_event.quantity_delta)
            sold_cost = abs(sale_event.cost_delta_minor)
            prior_restored_cost = int(
                await db.scalar(
                    select(
                        func.coalesce(func.sum(StoreCostEvent.cost_delta_minor), 0)
                    ).where(
                        StoreCostEvent.variant_id == order_item.variant_id,
                        StoreCostEvent.event_kind == "return_restock",
                        StoreCostEvent.evidence
                        == f"return-order:{order.id}:{order_item.id}",
                    )
                )
                or 0
            )
            if prior_returned + req_line.quantity == sold_qty:
                restored_cost_minor = max(0, sold_cost - prior_restored_cost)
            else:
                restored_cost_minor = sold_cost * req_line.quantity // sold_qty

        if req_line.condition == "restockable":
            inventory = await db.scalar(
                select(InventoryItem)
                .where(InventoryItem.variant_id == order_item.variant_id)
                .with_for_update()
            )
            if inventory is None:
                raise HTTPException(409, "Inventory item missing for returned variant")
            valuation = await db.scalar(
                select(StoreInventoryValuation)
                .where(StoreInventoryValuation.variant_id == order_item.variant_id)
                .with_for_update()
            )
            if (
                valuation is not None
                and valuation.quantity == inventory.quantity_on_hand
                and restored_cost_minor > 0
            ):
                valuation.quantity += req_line.quantity
                valuation.total_cost_minor += restored_cost_minor
            inventory.quantity_on_hand += req_line.quantity
            db.add(
                InventoryMovement(
                    inventory_item_id=inventory.id,
                    movement_type=InventoryMovementType.RETURN,
                    quantity=req_line.quantity,
                    reference_type="order_return",
                    reference_id=order.id,
                    notes=f"{clean_ref}: {payload.reason}",
                    performed_by=owner.auth_id or str(owner.id),
                )
            )
            db.add(
                StoreCostEvent(
                    variant_id=order_item.variant_id,
                    event_kind="return_restock",
                    quantity_delta=req_line.quantity,
                    cost_delta_minor=restored_cost_minor,
                    reference=event_ref,
                    evidence=f"return-order:{order.id}:{order_item.id}",
                    actor_auth_id=owner.auth_id or str(owner.id),
                )
            )
            total_restored_cost_minor += restored_cost_minor
            results.append(
                {
                    "order_item_id": order_item.id,
                    "condition": "restockable",
                    "quantity": req_line.quantity,
                    "restored_cost_minor": restored_cost_minor,
                }
            )
        else:
            db.add(
                StoreCostEvent(
                    variant_id=order_item.variant_id,
                    event_kind="return_damaged",
                    quantity_delta=-req_line.quantity,
                    cost_delta_minor=0,
                    reference=event_ref,
                    evidence=f"return-order:{order.id}:{order_item.id}",
                    actor_auth_id=owner.auth_id or str(owner.id),
                )
            )
            results.append(
                {
                    "order_item_id": order_item.id,
                    "condition": "damaged",
                    "quantity": req_line.quantity,
                    "restored_cost_minor": 0,
                }
            )

    await db.commit()

    entry_id, sync_status = (None, "not_required")
    if total_restored_cost_minor > 0:
        entry_id, sync_status = await post_return_cogs_reversal_journal(
            return_reference=clean_ref,
            order_id=order.id,
            order_number=order.order_number,
            entry_date=date.today(),
            restored_cost_minor=total_restored_cost_minor,
        )

    return {
        "order_id": order.id,
        "order_number": order.order_number,
        "reference": clean_ref,
        "total_restored_cost_minor": total_restored_cost_minor,
        "ledger_entry_id": entry_id,
        "ledger_sync_status": sync_status,
        "lines": results,
    }
