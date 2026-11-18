"""Store purchase order -> independent approval -> verified goods receipt.

Never pays suppliers, edits checkout payment status or fabricates inventory cost.
"""
import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from pydantic import BaseModel, Field
from services.ledger_service.app.deps import require_ledger_role
from services.ledger_service.models.enums import LedgerRole
from services.store_service.models import InventoryItem, InventoryMovement, InventoryMovementType, ProductVariant, Supplier
from services.store_service.models.procurement import (
    StoreCostEvent, StoreGoodsReceipt, StoreGoodsReceiptLine,
    StoreInventoryValuation, StoreOpeningReconciliation,
    StoreProcurementGrant, StorePurchaseOrder, StorePurchaseOrderLine,
    StoreSupplierInvoice, StoreTradeReceivable,
)
from services.store_service.routers.procurement_access import procurement_reader, procurement_writer
from sqlalchemy import select, func
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
    lines: list[ReceiveLineRequest] = Field(min_length=1, max_length=100)


class SupplierInvoiceRequest(BaseModel):
    reference: str = Field(min_length=2, max_length=120)
    total_minor: int = Field(gt=0)
    due_on: date | None = None


class OpeningRequest(BaseModel):
    variant_id: uuid.UUID
    source_reference: str = Field(min_length=5, max_length=255)
    quantity: int = Field(ge=0)
    total_cost_minor: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=1000)


class ReceivableRequest(BaseModel):
    order_id: uuid.UUID
    due_on: date
    reason: str = Field(min_length=10, max_length=1000)


@router.put("/grants")
async def upsert_procurement_grant(
    payload: GrantRequest,
    owner=Depends(require_ledger_role(LedgerRole.OWNER)),
    db: AsyncSession = Depends(get_async_db),
):
    if payload.auth_id == owner.auth_id and not payload.active:
        raise HTTPException(409, "Owner cannot revoke own current operational access here")
    grant = await db.scalar(select(StoreProcurementGrant).where(
        StoreProcurementGrant.auth_id == payload.auth_id,
    ).with_for_update())
    if grant is None:
        grant = StoreProcurementGrant(auth_id=payload.auth_id, granted_by=owner.auth_id or str(owner.id))
        db.add(grant)
    grant.role, grant.active = payload.role, payload.active
    await db.commit()
    return {"auth_id": grant.auth_id, "role": grant.role, "active": grant.active}


@router.get("/orders")
async def list_purchase_orders(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    orders = (await db.scalars(select(StorePurchaseOrder).order_by(
        StorePurchaseOrder.created_at.desc(),
    ).limit(150))).all()
    return [
        {"id": o.id, "order_number": o.order_number, "supplier_id": o.supplier_id,
         "status": o.status, "requested_by": o.requested_by, "approved_by": o.approved_by}
        for o in orders
    ]


@router.post("/orders", status_code=201)
async def create_purchase_order(
    payload: PurchaseOrderRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    supplier = await db.scalar(select(Supplier.id).where(
        Supplier.id == payload.supplier_id, Supplier.is_active.is_(True),
    ))
    if supplier is None:
        raise HTTPException(422, "Supplier is missing or inactive")
    ids = [line.variant_id for line in payload.lines]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Combine duplicate variants into one purchase line")
    known = set((await db.scalars(select(ProductVariant.id).where(
        ProductVariant.id.in_(ids), ProductVariant.is_active.is_(True),
    ))).all())
    if known != set(ids):
        raise HTTPException(422, "Purchase contains unknown or inactive variants")
    order_id = uuid.uuid4()
    number = "PO-" + order_id.hex[:16].upper()
    db.add(StorePurchaseOrder(
        id=order_id, supplier_id=payload.supplier_id, order_number=number,
        description=payload.description, requested_by=actor.user_id,
    ))
    for line in payload.lines:
        db.add(StorePurchaseOrderLine(
            order_id=order_id, variant_id=line.variant_id,
            quantity=line.quantity, unit_landed_minor=line.landed_unit_cost_minor,
        ))
    await db.commit()
    return {"id": order_id, "number": number, "status": "draft"}


@router.post("/orders/{order_id}/submit")
async def submit_purchase_order(
    order_id: uuid.UUID,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(select(StorePurchaseOrder).where(
        StorePurchaseOrder.id == order_id,
    ).with_for_update())
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
    owner=Depends(require_ledger_role(LedgerRole.OWNER)),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(select(StorePurchaseOrder).where(
        StorePurchaseOrder.id == order_id,
    ).with_for_update())
    if item is None:
        raise HTTPException(404, "Purchase order not found")
    if item.status != "submitted":
        raise HTTPException(409, "Order must be submitted before independent decision")
    if owner.auth_id == item.requested_by:
        raise HTTPException(403, "The purchase requester cannot approve their own request")
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
    item = await db.scalar(select(StorePurchaseOrder).where(
        StorePurchaseOrder.id == order_id,
    ).with_for_update())
    if item is None or item.status not in ("approved", "partially_received"):
        raise HTTPException(409, "Receive goods only against an approved purchase")
    prior = await db.scalar(select(StoreGoodsReceipt.id).where(
        StoreGoodsReceipt.reference == payload.reference,
    ))
    if prior:
        raise HTTPException(409, "Goods receipt reference already recorded")
    incoming_ids = [line.purchase_line_id for line in payload.lines]
    if len(incoming_ids) != len(set(incoming_ids)):
        raise HTTPException(422, "Repeated line in receipt")
    lines = (await db.scalars(select(StorePurchaseOrderLine).where(
        StorePurchaseOrderLine.order_id == order_id,
    ).order_by(StorePurchaseOrderLine.id).with_for_update())).all()
    lookup = {line.id: line for line in lines}
    if not set(incoming_ids).issubset(lookup):
        raise HTTPException(422, "Receipt includes a line from a different order")
    receipt_id = uuid.uuid4()
    receipt = StoreGoodsReceipt(
        id=receipt_id, order_id=order_id,
        reference=payload.reference, evidence_reference=payload.evidence_reference,
        actor_auth_id=actor.user_id,
    )
    db.add(receipt)
    for line in payload.lines:
        po_line = lookup[line.purchase_line_id]
        if po_line.received + line.quantity > po_line.quantity:
            raise HTTPException(409, "Received quantity exceeds approved purchase order")
        inventory = await db.scalar(select(InventoryItem).where(
            InventoryItem.variant_id == po_line.variant_id,
        ).with_for_update())
        if inventory is None:
            inventory = InventoryItem(variant_id=po_line.variant_id, quantity_on_hand=0, quantity_reserved=0)
            db.add(inventory)
            await db.flush()
        valuation = await db.scalar(select(StoreInventoryValuation).where(
            StoreInventoryValuation.variant_id == po_line.variant_id,
        ).with_for_update())
        if valuation is None:
            valuation = StoreInventoryValuation(variant_id=po_line.variant_id, quantity=0, total_cost_minor=0)
            db.add(valuation)
            await db.flush()
        if valuation.quantity != inventory.quantity_on_hand:
            raise HTTPException(409, "Existing stock must have an approved valuation before receiving new goods")
        cost = line.quantity * po_line.unit_landed_minor
        inventory.quantity_on_hand += line.quantity
        inventory.last_restock_at = utc_now()
        valuation.quantity += line.quantity
        valuation.total_cost_minor += cost
        po_line.received += line.quantity
        db.add(StoreGoodsReceiptLine(
            receipt_id=receipt_id, purchase_line_id=po_line.id,
            quantity=line.quantity, unit_landed_minor=po_line.unit_landed_minor,
        ))
        db.add(StoreCostEvent(
            variant_id=po_line.variant_id, event_kind="receipt",
            quantity_delta=line.quantity, cost_delta_minor=cost,
            reference=f"receipt:{receipt_id}:{po_line.id}",
            evidence=payload.evidence_reference, actor_auth_id=actor.user_id,
        ))
        db.add(InventoryMovement(
            inventory_item_id=inventory.id, movement_type=InventoryMovementType.RESTOCK,
            quantity=line.quantity, reference_type="goods_receipt", reference_id=receipt_id,
            notes=payload.evidence_reference, performed_by=actor.user_id,
        ))
    item.status = "received" if all(l.received == l.quantity for l in lines) else "partially_received"
    await db.commit()
    return {"id": receipt_id, "order_id": order_id, "status": item.status}


@router.post("/orders/{order_id}/invoices", status_code=201)
async def record_supplier_invoice(
    order_id: uuid.UUID,
    payload: SupplierInvoiceRequest,
    actor: AuthUser = Depends(procurement_writer),
    db: AsyncSession = Depends(get_async_db),
):
    item = await db.scalar(select(StorePurchaseOrder).where(StorePurchaseOrder.id == order_id))
    if not item or item.status not in ("approved", "partially_received", "received"):
        raise HTTPException(409, "Supplier invoice requires an approved order")
    invoice = StoreSupplierInvoice(
        supplier_id=item.supplier_id, order_id=order_id,
        reference=payload.reference, total_minor=payload.total_minor,
        due_on=payload.due_on, created_by=actor.user_id,
    )
    db.add(invoice)
    await db.commit()
    return {"id": invoice.id, "total_minor": invoice.total_minor, "verified_paid_minor": 0}


@router.get("/liabilities")
async def supplier_liabilities(
    _actor: AuthUser = Depends(procurement_reader),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (await db.scalars(select(StoreSupplierInvoice).order_by(
        StoreSupplierInvoice.id,
    ).limit(300))).all()
    return [{"id": row.id, "supplier_id": row.supplier_id, "order_id": row.order_id,
             "reference": row.reference, "due_on": row.due_on,
             "outstanding_minor": row.total_minor - row.verified_paid_minor} for row in rows]
