"""Invoice issuance + gapless numbering (design §13, R5-PR1).

Numbers are allocated by an atomic ``INSERT ... ON CONFLICT DO UPDATE ...
RETURNING`` on ``invoice_sequences`` keyed by (org, prefix, year): concurrent
issues serialise on that row, so the sequence is gapless across committed
transactions (a rolled-back issue releases its number). Voiding an invoice keeps
the number (the void record is retained) — required for a gapless audit trail.

Tax is deferred: every line's ``tax_minor`` is 0 and ``total = subtotal`` until
VAT/WHT determination lands (R5-PR2+).
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.datetime_utils import utc_now
from services.ledger_service.models import Invoice, InvoiceLine, InvoiceSequence
from services.ledger_service.schemas.invoice import (
    InvoiceCreate,
    InvoiceList,
    InvoiceListItem,
    InvoiceOut,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class InvoiceNotFound(Exception):
    """No invoice with that id in this org."""


async def _next_invoice_number(
    session: AsyncSession, org_id: uuid.UUID, prefix: str, year: int
) -> str:
    """Allocate the next gapless number for (org, prefix, year), atomically."""
    stmt = (
        pg_insert(InvoiceSequence)
        .values(org_id=org_id, prefix=prefix, year=year, last_number=1)
        .on_conflict_do_update(
            constraint="uq_invoice_sequence",
            set_={
                "last_number": InvoiceSequence.last_number + 1,
                "updated_at": utc_now(),
            },
        )
        .returning(InvoiceSequence.last_number)
    )
    n = (await session.execute(stmt)).scalar_one()
    return f"{prefix}-{year}-{int(n):06d}"


async def create_invoice(
    session: AsyncSession, org_id: uuid.UUID, payload: InvoiceCreate
) -> InvoiceOut:
    """Issue an invoice: allocate a number, persist header + lines. Caller commits."""
    issue = payload.issue_date or utc_now().date()
    prefix = (payload.prefix or "SB").strip() or "SB"
    number = await _next_invoice_number(session, org_id, prefix, issue.year)

    inv = Invoice(
        org_id=org_id,
        invoice_number=number,
        status=payload.status or "issued",
        source_service=payload.source_service,
        source_type=payload.source_type,
        source_id=payload.source_id,
        customer_ref=payload.customer_ref,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        customer_tin=payload.customer_tin,
        currency=payload.currency or "NGN",
        issue_date=issue,
        due_date=payload.due_date,
        notes=payload.notes,
        invoice_metadata=payload.metadata,
    )

    subtotal = 0
    for i, line in enumerate(payload.lines):
        amount = (
            line.amount_minor
            if line.amount_minor is not None
            else line.unit_price_minor * line.quantity
        )
        subtotal += amount
        inv.lines.append(
            InvoiceLine(
                org_id=org_id,
                position=i,
                description=line.description,
                quantity=line.quantity,
                unit_price_minor=line.unit_price_minor,
                amount_minor=amount,
                tax_minor=0,
                dimension_1=line.dimension_1,
            )
        )

    inv.subtotal_minor = subtotal
    inv.tax_minor = 0
    inv.total_minor = subtotal  # + tax_minor once VAT lands
    session.add(inv)
    await session.flush()
    # lines were appended in-memory, so this serialises without a lazy load.
    return InvoiceOut.model_validate(inv)


async def get_invoice(
    session: AsyncSession, org_id: uuid.UUID, invoice_id: uuid.UUID
) -> InvoiceOut:
    inv = (
        await session.execute(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.id == invoice_id)
            .options(selectinload(Invoice.lines))
        )
    ).scalar_one_or_none()
    if inv is None:
        raise InvoiceNotFound(str(invoice_id))
    return InvoiceOut.model_validate(inv)


async def list_invoices(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> InvoiceList:
    base = select(Invoice).where(Invoice.org_id == org_id)
    if status:
        base = base.where(Invoice.status == status)
    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar() or 0
    rows = (
        (
            await session.execute(
                base.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return InvoiceList(
        items=[InvoiceListItem.model_validate(r) for r in rows], total=int(total)
    )


async def void_invoice(
    session: AsyncSession,
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    reason: Optional[str],
) -> InvoiceOut:
    """Void an invoice (keeps its number for the gapless audit trail). Idempotent."""
    inv = (
        await session.execute(
            select(Invoice)
            .where(Invoice.org_id == org_id, Invoice.id == invoice_id)
            .options(selectinload(Invoice.lines))
        )
    ).scalar_one_or_none()
    if inv is None:
        raise InvoiceNotFound(str(invoice_id))
    if inv.status != "void":
        inv.status = "void"
        inv.voided_at = utc_now()
        inv.void_reason = reason
        await session.flush()
    return InvoiceOut.model_validate(inv)
