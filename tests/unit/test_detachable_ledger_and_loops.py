"""Unit tests for ADR-023 detachable Ledger, gateway Finance Intelligence, and Store loops."""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from services.gateway_service.app.routers import finance_intelligence as fi
from services.ledger_service.app import deps as ledger_deps
from services.ledger_service.schemas.treasury import (
    BudgetCreate,
    PostConfirmedRequest,
    RequestCreate,
    SnapshotCreate,
)
from services.ledger_service.services.accounts import (
    list_coa_templates,
    load_coa_template,
)
from services.store_service.routers.procurement import (
    GoodsReceiptRequest,
    OrderReturnRequest,
    ReceivablePaymentRequest,
    ReturnLineRequest,
    SupplierInvoiceSettlementRequest,
)
from services.store_service.services import ledger_sync


def test_generic_smb_and_sports_club_coa_templates_load():
    templates = list_coa_templates()
    assert "generic_smb" in templates
    assert "sports_club" in templates

    smb = load_coa_template("generic_smb")
    by_code = {a["code"]: a for a in smb}
    assert by_code["1110"].get("is_bank_account") is True
    assert by_code["6100"].get("system_managed") is True
    assert by_code["2310"].get("recognition_target_ref") == "revenue_subscriptions"
    assert by_code["2310"].get("default_recognition_days") == 30
    # Zero swimming terminology in generic_smb template
    raw_names = " ".join(a["name"].lower() for a in smb)
    assert "swim" not in raw_names
    assert "academy" not in raw_names
    assert "coach" not in raw_names


def test_parse_header_org_id_validates_uuid():
    valid = uuid.uuid4()
    req = Request(
        {"type": "http", "headers": [(b"x-ledger-org-id", str(valid).encode())]}
    )
    assert ledger_deps.parse_header_org_id(req) == valid

    empty_req = Request({"type": "http", "headers": []})
    assert ledger_deps.parse_header_org_id(empty_req) is None

    bad_req = Request(
        {"type": "http", "headers": [(b"x-ledger-org-id", b"not-a-uuid")]}
    )
    with pytest.raises(HTTPException) as exc:
        ledger_deps.parse_header_org_id(bad_req)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_resolve_org_refuses_to_guess_when_multiple_orgs_exist(monkeypatch):
    monkeypatch.setattr(ledger_deps, "_env_org_id", lambda: None)
    monkeypatch.setattr(ledger_deps, "_FALLBACK_ORG_ID", None)
    fake_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [uuid.uuid4(), uuid.uuid4()])
    )
    fake_session = SimpleNamespace(execute=AsyncMock(return_value=fake_result))
    with pytest.raises(HTTPException) as exc:
        await ledger_deps._resolve_org_id(fake_session)
    assert exc.value.status_code == 503


def test_treasury_schemas_support_liability_settlement_and_media_evidence():
    media_id = uuid.uuid4()
    req = RequestCreate(
        kind="liability_settlement",
        amount_minor=250_000,
        purpose="Settle supplier invoice INV-01",
        category="supplier_payable",
        source_bank_id=uuid.uuid4(),
        invoice_reference="INV-01",
        evidence_media_id=media_id,
    )
    assert req.kind == "liability_settlement"
    assert req.evidence_media_id == media_id

    # Liability settlement must not accept a destination bank
    with pytest.raises(ValidationError):
        RequestCreate(
            kind="liability_settlement",
            amount_minor=100_000,
            purpose="Invalid transfer",
            category="supplier_payable",
            source_bank_id=uuid.uuid4(),
            destination_bank_id=uuid.uuid4(),
        )

    budget = BudgetCreate(
        kind="liability_settlement",
        name="Vendor payables Q4",
        category="supplier_payable",
        amount_minor=500_000,
        starts_on=date(2026, 11, 1),
        ends_on=date(2026, 12, 31),
    )
    assert budget.kind == "liability_settlement"

    snap = SnapshotCreate(
        as_of=date(2026, 11, 25),
        balance_minor=1_200_000,
        evidence_reference="stanbic-stmt-nov25",
        evidence_media_id=media_id,
    )
    assert snap.evidence_media_id == media_id

    post_req = PostConfirmedRequest(liability_account_code="2110", acknowledgement=True)
    assert post_req.liability_account_code == "2110"


@pytest.mark.asyncio
async def test_gateway_finance_intelligence_fails_closed_without_ledger_membership(
    monkeypatch,
):
    req = Request(
        {"type": "http", "headers": [(b"authorization", b"Bearer bad-token")]}
    )

    resp = httpx.Response(
        403,
        json={"detail": "Insufficient ledger role"},
        request=httpx.Request("GET", "http://ledger/admin/finance/users/me"),
    )
    client_mock = AsyncMock()
    client_mock.__aenter__.return_value = SimpleNamespace(
        get=AsyncMock(return_value=resp)
    )
    monkeypatch.setattr(fi.httpx, "AsyncClient", lambda **kwargs: client_mock)
    monkeypatch.setattr(
        fi,
        "get_settings",
        lambda: SimpleNamespace(LEDGER_SERVICE_URL="http://ledger"),
    )
    with pytest.raises(HTTPException) as exc:
        await fi.company_finance_intelligence(req)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_gateway_finance_intelligence_aggregates_service_summaries(monkeypatch):
    req = Request(
        {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer valid-token"),
                (b"x-ledger-org-id", str(uuid.uuid4()).encode()),
            ],
        }
    )

    monkeypatch.setattr(
        fi, "_verify_ledger_viewer", AsyncMock(return_value={"role": "viewer"})
    )
    monkeypatch.setattr(
        fi,
        "get_settings",
        lambda: SimpleNamespace(
            PAYMENTS_SERVICE_URL="http://payments",
            ACADEMY_SERVICE_URL="http://academy",
            MEMBERS_SERVICE_URL="http://members",
            STORE_SERVICE_URL="http://store",
        ),
    )

    async def fake_internal_get(*, service_url, path, calling_service, timeout=10.0):
        assert calling_service == "gateway"
        payloads = {
            "/internal/payments/finance-summary": {
                "successful_checkout_groups": [],
                "posted_paystack_settlements": {
                    "count": 1,
                    "gross_minor": 1000,
                    "net_minor": 980,
                    "fees_minor": 20,
                },
                "unposted_paystack_settlement_batches": 0,
                "warning": "ok",
            },
            "/internal/academy/finance-summary": {
                "active_enrollments": 12,
                "dropout_pending_enrollments": 2,
                "reviewed_class_plans": 10,
                "enrollments_without_class_plan": 4,
                "remaining_prepaid_classes_with_reviewed_plans": 48,
                "pending_or_missed_installments_minor": 900_000,
                "warning": "ok",
            },
            "/internal/members/club-finance-summary": {
                "currently_active_enrollments": 25,
                "future_service_costs_minor": None,
                "warning": "ok",
            },
            "/internal/store/finance-summary": {
                "order_status_totals": [],
                "valued_inventory_minor": 500_000,
                "partial_valued_inventory_minor": 500_000,
                "unvalued_sku_count": 0,
                "unvalued_fulfilled_orders_count": 0,
                "supplier_liabilities_minor": 100_000,
                "approved_credit_exposure_minor": 150_000,
                "warning": "ok",
            },
        }
        return httpx.Response(
            200,
            json=payloads[path],
            request=httpx.Request("GET", f"{service_url}{path}"),
        )

    monkeypatch.setattr(fi, "internal_get", fake_internal_get)
    report = await fi.company_finance_intelligence(req)
    assert report["degraded_domains"] == []
    assert report["academy"]["dropout_pending_enrollments"] == 2
    assert report["academy"]["pending_or_missed_installments_minor"] == 900_000
    assert report["club"]["currently_active_enrollments"] == 25
    assert report["store"]["unvalued_fulfilled_orders_count"] == 0


@pytest.mark.asyncio
async def test_store_ledger_sync_emits_balanced_journals(monkeypatch):
    posted_payloads = []

    async def fake_post(**kwargs):
        lines = kwargs["lines"]
        assert sum(line.get("debit", 0) for line in lines) == sum(
            line.get("credit", 0) for line in lines
        )
        posted_payloads.append(kwargs)
        return {"entry_id": str(uuid.uuid4()), "status": "posted"}

    monkeypatch.setattr(ledger_sync, "post_journal_entry", fake_post)

    e1, s1 = await ledger_sync.post_goods_receipt_journal(
        receipt_id=uuid.uuid4(),
        order_number="PO-123",
        entry_date=date(2026, 11, 25),
        total_cost_minor=150_000,
        evidence_reference="DEL-001",
    )
    e2, s2 = await ledger_sync.post_opening_inventory_journal(
        opening_id=uuid.uuid4(),
        variant_id=uuid.uuid4(),
        entry_date=date(2026, 11, 25),
        total_cost_minor=200_000,
        source_reference="OPEN-001",
    )
    e3, s3 = await ledger_sync.post_order_cogs_journal(
        order_id=uuid.uuid4(),
        order_number="ORD-1",
        entry_date=date(2026, 11, 25),
        total_cogs_minor=75_000,
    )
    e4, s4 = await ledger_sync.post_return_cogs_reversal_journal(
        return_reference="RET-1",
        order_id=uuid.uuid4(),
        order_number="ORD-1",
        entry_date=date(2026, 11, 25),
        restored_cost_minor=25_000,
    )
    e5, s5 = await ledger_sync.post_credit_sale_approval_journal(
        receivable_id=uuid.uuid4(),
        order_id=uuid.uuid4(),
        order_number="ORD-2",
        entry_date=date(2026, 11, 25),
        amount_minor=1_500_000,
        member_auth_id="user-1",
    )
    e6, s6 = await ledger_sync.post_credit_sale_payment_journal(
        payment_id=uuid.uuid4(),
        receivable_id=uuid.uuid4(),
        order_number="ORD-2",
        entry_date=date(2026, 11, 25),
        amount_minor=500_000,
        bank_account_ref="bank_operating_ngn",
        bank_reference="STANBIC-991",
        member_auth_id="user-1",
    )

    assert all(status == "posted" for status in (s1, s2, s3, s4, s5, s6))
    assert all(entry_id is not None for entry_id in (e1, e2, e3, e4, e5, e6))
    assert len(posted_payloads) == 6


def test_store_procurement_new_payload_schemas():
    media_id = uuid.uuid4()
    gr = GoodsReceiptRequest(
        reference="GR-2026-01",
        evidence_reference="WAYBILL-01",
        evidence_media_id=media_id,
        lines=[{"purchase_line_id": uuid.uuid4(), "quantity": 5}],
    )
    assert gr.evidence_media_id == media_id

    settle = SupplierInvoiceSettlementRequest(
        amount_minor=50_000,
        bank_reference="STB-REF-1",
        ledger_journal_entry_id=uuid.uuid4(),
        evidence_media_id=media_id,
    )
    assert settle.amount_minor == 50_000

    repay = ReceivablePaymentRequest(
        amount_minor=150_000,
        bank_reference="STB-CRD-1",
        bank_account_ref="bank_operating_ngn",
        evidence_media_id=media_id,
    )
    assert repay.bank_account_ref == "bank_operating_ngn"

    ret = OrderReturnRequest(
        reference="RET-001",
        reason="Wrong goggle size returned unopened",
        lines=[
            ReturnLineRequest(
                order_item_id=uuid.uuid4(), quantity=1, condition="restockable"
            )
        ],
    )
    assert ret.lines[0].condition == "restockable"


def test_media_finance_document_purpose_is_private():
    from services.media_service.services.storage import (
        BucketType,
        get_bucket_for_purpose,
    )

    assert get_bucket_for_purpose("finance_document") == BucketType.PRIVATE


@pytest.mark.asyncio
async def test_credit_approved_unpaid_order_unlocks_fulfillment(monkeypatch):
    from libs.auth.models import AuthUser
    from services.store_service.models import OrderStatus
    from services.store_service.routers.admin_inventory import _helpers

    order = SimpleNamespace(
        id=uuid.uuid4(),
        order_number="ORD-CRD-1",
        status=OrderStatus.PENDING_PAYMENT,
        admin_notes=None,
        member_auth_id=None,
        wallet_hold_id=None,
        wallet_transaction_id=None,
        bubbles_applied=0,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=uuid.uuid4()),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(_helpers, "log_audit", AsyncMock())
    actor = AuthUser(
        user_id="admin-1", role="authenticated", app_metadata={"roles": ["admin"]}
    )
    await _helpers._apply_order_status_change(
        db, order, OrderStatus.PROCESSING, "Credit approved handover", actor
    )
    assert order.status == OrderStatus.PROCESSING
    db.commit.assert_awaited_once()
