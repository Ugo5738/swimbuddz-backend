"""Idempotent Ledger posting helpers for Store Procurement, Inventory & Credit.

Strictly communicates with ``ledger_service`` via ``libs.common.ledger_client``
over authenticated HTTP — never imports ``ledger_service`` packages.

Every posting returns ``(entry_id, sync_status)`` where ``sync_status`` is
``"posted"`` on success or ``"pending_retry"`` if Ledger is temporarily
unreachable. Failures are always logged loudly and stored on the operational
row so Finance Owners can replay them idempotently without losing operational
data or silently corrupting the books.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def _emit_safe(
    *,
    entry_date: date,
    description: str,
    source_type: str,
    source_id: str,
    lines: list[dict],
    metadata: Optional[dict] = None,
) -> tuple[Optional[uuid.UUID], str]:
    try:
        result = await post_journal_entry(
            entry_date=entry_date,
            description=description,
            source_service="store",
            source_type=source_type,
            source_id=source_id,
            lines=lines,
            calling_service="store",
            metadata=metadata or {},
        )
        raw_id = result.get("entry_id")
        return (uuid.UUID(str(raw_id)) if raw_id else None), "posted"
    except Exception as exc:
        logger.error(
            "Store->Ledger posting failed (source_type=%s source_id=%s): %s",
            source_type,
            source_id,
            exc,
        )
        return None, "pending_retry"


async def post_goods_receipt_journal(
    *,
    receipt_id: uuid.UUID,
    order_number: str,
    entry_date: date,
    total_cost_minor: int,
    evidence_reference: str,
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR store_inventory / CR accounts_payable for verified goods received."""
    if total_cost_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Store goods receipt ({order_number}) — {evidence_reference}",
        source_type="goods_receipt",
        source_id=str(receipt_id),
        lines=[
            {
                "account_ref": "store_inventory",
                "debit": total_cost_minor,
                "dimension_1": "store",
                "external_ref": evidence_reference,
                "description": f"Inventory received on {order_number}",
            },
            {
                "account_ref": "accounts_payable",
                "credit": total_cost_minor,
                "dimension_1": "store",
                "external_ref": evidence_reference,
                "description": f"Accrued payable on {order_number}",
            },
        ],
        metadata={"receipt_id": str(receipt_id), "order_number": order_number},
    )


async def post_opening_inventory_journal(
    *,
    opening_id: uuid.UUID,
    variant_id: uuid.UUID,
    entry_date: date,
    total_cost_minor: int,
    source_reference: str,
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR store_inventory / CR retained_earnings for approved opening stock."""
    if total_cost_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Approved opening stock valuation ({source_reference})",
        source_type="opening_inventory",
        source_id=str(opening_id),
        lines=[
            {
                "account_ref": "store_inventory",
                "debit": total_cost_minor,
                "dimension_1": "store",
                "external_ref": source_reference,
                "description": f"Opening inventory valuation for variant {variant_id}",
            },
            {
                "account_ref": "retained_earnings",
                "credit": total_cost_minor,
                "dimension_1": "store",
                "external_ref": source_reference,
                "description": "Opening inventory equity offset",
            },
        ],
        metadata={"opening_id": str(opening_id), "variant_id": str(variant_id)},
    )


async def post_order_cogs_journal(
    *,
    order_id: uuid.UUID,
    order_number: str,
    entry_date: date,
    total_cogs_minor: int,
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR cogs_store / CR store_inventory when a valued order is fulfilled."""
    if total_cogs_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Store COGS for fulfilled order {order_number}",
        source_type="order_cogs",
        source_id=str(order_id),
        lines=[
            {
                "account_ref": "cogs_store",
                "debit": total_cogs_minor,
                "dimension_1": "store",
                "external_ref": order_number,
                "description": f"Cost of goods sold — {order_number}",
            },
            {
                "account_ref": "store_inventory",
                "credit": total_cogs_minor,
                "dimension_1": "store",
                "external_ref": order_number,
                "description": f"Inventory consumed — {order_number}",
            },
        ],
        metadata={"order_id": str(order_id), "order_number": order_number},
    )


async def post_return_cogs_reversal_journal(
    *,
    return_reference: str,
    order_id: uuid.UUID,
    order_number: str,
    entry_date: date,
    restored_cost_minor: int,
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR store_inventory / CR cogs_store when restockable goods are returned."""
    if restored_cost_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Store return COGS reversal for order {order_number} ({return_reference})",
        source_type="order_return_cogs",
        source_id=return_reference,
        lines=[
            {
                "account_ref": "store_inventory",
                "debit": restored_cost_minor,
                "dimension_1": "store",
                "external_ref": return_reference,
                "description": f"Restocked inventory — {order_number}",
            },
            {
                "account_ref": "cogs_store",
                "credit": restored_cost_minor,
                "dimension_1": "store",
                "external_ref": return_reference,
                "description": f"COGS reversed on return — {order_number}",
            },
        ],
        metadata={
            "order_id": str(order_id),
            "order_number": order_number,
            "return_reference": return_reference,
        },
    )


async def post_credit_sale_approval_journal(
    *,
    receivable_id: uuid.UUID,
    order_id: uuid.UUID,
    order_number: str,
    entry_date: date,
    amount_minor: int,
    member_auth_id: Optional[str],
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR accounts_receivable / CR revenue_store on independent credit approval."""
    if amount_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Approved store credit sale — {order_number}",
        source_type="credit_sale_approved",
        source_id=str(receivable_id),
        lines=[
            {
                "account_ref": "accounts_receivable",
                "debit": amount_minor,
                "dimension_1": "store",
                "member_ref": member_auth_id,
                "external_ref": order_number,
                "description": f"Customer credit receivable — {order_number}",
            },
            {
                "account_ref": "revenue_store",
                "credit": amount_minor,
                "dimension_1": "store",
                "member_ref": member_auth_id,
                "external_ref": order_number,
                "description": f"Store credit sale revenue — {order_number}",
            },
        ],
        metadata={
            "receivable_id": str(receivable_id),
            "order_id": str(order_id),
            "order_number": order_number,
        },
    )


async def post_credit_sale_payment_journal(
    *,
    payment_id: uuid.UUID,
    receivable_id: uuid.UUID,
    order_number: str,
    entry_date: date,
    amount_minor: int,
    bank_account_ref: str,
    bank_reference: str,
    member_auth_id: Optional[str],
) -> tuple[Optional[uuid.UUID], str]:
    """Post DR <bank_account_ref> / CR accounts_receivable on verified credit repayment."""
    if amount_minor <= 0:
        return None, "not_required"
    return await _emit_safe(
        entry_date=entry_date,
        description=f"Verified store credit repayment — {order_number} ({bank_reference})",
        source_type="credit_sale_payment",
        source_id=str(payment_id),
        lines=[
            {
                "account_ref": bank_account_ref,
                "debit": amount_minor,
                "dimension_1": "store",
                "member_ref": member_auth_id,
                "external_ref": bank_reference,
                "description": f"Bank credit receipt — {order_number}",
            },
            {
                "account_ref": "accounts_receivable",
                "credit": amount_minor,
                "dimension_1": "store",
                "member_ref": member_auth_id,
                "external_ref": bank_reference,
                "description": f"Receivable settled — {order_number}",
            },
        ],
        metadata={
            "payment_id": str(payment_id),
            "receivable_id": str(receivable_id),
            "order_number": order_number,
            "bank_reference": bank_reference,
        },
    )
