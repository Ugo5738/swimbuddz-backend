"""Invoice issuance tests (R5-PR1): gapless numbering, totals, void, read.

All run in the rolled-back db_session — sequence increments roll back too, so
numbers are asserted as consecutive (robust to any committed state) rather than
pinned to 000001.
"""

import uuid

from libs.common.config import get_settings
from services.ledger_service.schemas.invoice import InvoiceCreate, InvoiceLineIn
from services.ledger_service.services.invoices import (
    create_invoice,
    get_invoice,
    list_invoices,
    void_invoice,
)
from sqlalchemy import text


async def _org_id(db_session) -> uuid.UUID:
    return uuid.UUID((get_settings().LEDGER_DEFAULT_ORG_ID or "").strip())


async def _ctx(db_session, org_id) -> None:
    await db_session.execute(
        text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
    )


def _payload(**kw) -> InvoiceCreate:
    base = dict(
        customer_name="Acme Corp",
        lines=[
            InvoiceLineIn(
                description="Academy cohort", quantity=2, unit_price_minor=50_000
            )
        ],
    )
    base.update(kw)
    return InvoiceCreate(**base)


def _seq(number: str) -> int:
    return int(number.rsplit("-", 1)[1])


async def test_invoice_numbering_is_gapless(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    a = await create_invoice(db_session, org_id, _payload())
    b = await create_invoice(db_session, org_id, _payload())
    c = await create_invoice(db_session, org_id, _payload())

    assert _seq(b.invoice_number) == _seq(a.invoice_number) + 1
    assert _seq(c.invoice_number) == _seq(b.invoice_number) + 1
    assert all(inv.invoice_number.startswith("SB-") for inv in (a, b, c))


async def test_create_invoice_computes_totals(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    inv = await create_invoice(
        db_session,
        org_id,
        _payload(
            lines=[
                InvoiceLineIn(description="A", quantity=2, unit_price_minor=50_000),
                InvoiceLineIn(description="B", quantity=1, unit_price_minor=25_000),
            ]
        ),
    )
    assert inv.subtotal_minor == 125_000
    assert inv.tax_minor == 0
    assert inv.total_minor == 125_000
    assert len(inv.lines) == 2
    assert inv.lines[0].amount_minor == 100_000


async def test_void_keeps_number_and_is_idempotent(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    inv = await create_invoice(db_session, org_id, _payload())
    voided = await void_invoice(db_session, org_id, inv.id, "duplicate")
    assert voided.status == "void"
    assert voided.invoice_number == inv.invoice_number  # number retained

    again = await void_invoice(db_session, org_id, inv.id, "again")
    assert again.status == "void"  # idempotent


async def test_get_and_list(db_session):
    org_id = await _org_id(db_session)
    await _ctx(db_session, org_id)

    inv = await create_invoice(db_session, org_id, _payload(customer_name="ListMe"))
    fetched = await get_invoice(db_session, org_id, inv.id)
    assert fetched.invoice_number == inv.invoice_number
    assert fetched.customer_name == "ListMe"

    listing = await list_invoices(db_session, org_id, limit=500)
    assert any(item.id == inv.id for item in listing.items)
