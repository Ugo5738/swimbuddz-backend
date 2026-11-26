"""Unit tests for shared Refund service, Paystack webhooks, and deduction-safe settlement ledger (ADR-023)."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.payments_service.models.enums import (
    PaymentPurpose,
    PaymentStatus,
    RefundMethod,
    RefundStatus,
)
from services.payments_service.schemas.refund import RefundCreateRequest
from services.payments_service.services import ledger_emit, refund_service


class TestResolveRefundMethod:
    def test_recent_paystack_payment_routes_to_paystack_refund(self):
        payment = SimpleNamespace(
            provider="paystack",
            payment_method="paystack",
            paid_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
        method, provider, age_fallback = refund_service.resolve_refund_method(
            payment,
            cash_refund_kobo=1_500_000,
            bubbles_refund=0,
            store_credit_refund_kobo=0,
        )
        assert method == RefundMethod.PAYSTACK_REFUND
        assert provider == "paystack"
        assert age_fallback is False

    def test_paystack_payment_older_than_1_year_falls_back_to_manual_bank_transfer(self):
        payment = SimpleNamespace(
            provider="paystack",
            payment_method="paystack",
            paid_at=datetime.now(timezone.utc) - timedelta(days=400),
        )
        method, provider, age_fallback = refund_service.resolve_refund_method(
            payment,
            cash_refund_kobo=1_500_000,
            bubbles_refund=0,
            store_credit_refund_kobo=0,
        )
        assert method == RefundMethod.MANUAL_BANK_TRANSFER
        assert provider == "manual_transfer"
        assert age_fallback is True

    def test_mixed_cash_and_bubbles_returns_mixed_with_paystack_provider(self):
        payment = SimpleNamespace(
            provider="paystack",
            payment_method="paystack",
            paid_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        method, provider, _ = refund_service.resolve_refund_method(
            payment,
            cash_refund_kobo=1_000_000,
            bubbles_refund=50,
            store_credit_refund_kobo=0,
        )
        assert method == RefundMethod.MIXED
        assert provider == "paystack"


class TestRefundInvariantsAndWebhooks:
    @pytest.mark.asyncio
    async def test_create_refund_rejects_over_refund_above_remaining_paid_balance(
        self, monkeypatch
    ):
        payment = SimpleNamespace(
            id=uuid.uuid4(),
            reference="store-order-123",
            status=PaymentStatus.PAID,
            amount=25000.0,
            provider="paystack",
            payment_method="paystack",
            payer_email="ada@swimbuddz.com",
            paid_at=datetime.now(timezone.utc),
        )
        # First execute: idempotency check (None); second: payment row lock
        exec_results = [
            SimpleNamespace(scalar_one_or_none=lambda: None),
            SimpleNamespace(scalar_one_or_none=lambda: payment),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=exec_results))
        monkeypatch.setattr(
            refund_service,
            "get_active_refunded_cash_kobo",
            AsyncMock(return_value=1_500_000),  # ₦15k already refunded
        )

        payload = RefundCreateRequest(
            idempotency_key="store-case:over-refund",
            payment_reference="store-order-123",
            member_auth_id="user-1",
            source_service="store",
            source_entity_type="store_order_case",
            source_entity_id=str(uuid.uuid4()),
            requested_total_kobo=1_200_000,
            cash_refund_kobo=1_200_000,  # ₦12k > ₦10k remaining!
        )
        with pytest.raises(HTTPException) as err:
            await refund_service.create_refund_record(db, payload)
        assert err.value.status_code == 409
        assert "exceeds remaining refundable" in err.value.detail

    @pytest.mark.asyncio
    async def test_apply_paystack_refund_processed_webhook_posts_ledger_and_notifies_store(
        self, monkeypatch
    ):
        refund = SimpleNamespace(
            id=uuid.uuid4(),
            refund_reference="REF-123456789ABC",
            payment_reference="store-order-123",
            source_service="store",
            source_entity_type="store_order_case",
            source_entity_id=str(uuid.uuid4()),
            purpose="store_order",
            currency="NGN",
            member_auth_id="user-1",
            cash_refund_kobo=1_500_000,
            bubbles_refund=0,
            store_credit_refund_kobo=0,
            provider="paystack",
            provider_refund_id="ps_ref_999",
            provider_status="pending",
            provider_payload=None,
            external_bank_reference=None,
            status=RefundStatus.INITIATED,
            ledger_accrual_posted=True,
            ledger_disbursement_posted=False,
            completed_at=None,
            failed_at=None,
            failure_reason=None,
        )
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(scalar_one_or_none=lambda: refund)
            ),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )
        ledger_mock = AsyncMock(return_value=True)
        notify_mock = AsyncMock()
        monkeypatch.setattr(
            refund_service, "emit_refund_completed_to_ledger", ledger_mock
        )
        monkeypatch.setattr(
            refund_service, "notify_source_service_refund_status", notify_mock
        )

        updated = await refund_service.apply_paystack_refund_event(
            db,
            event="refund.processed",
            data={
                "id": "ps_ref_999",
                "status": "processed",
                "transaction_reference": "store-order-123",
                "amount": 1_500_000,
            },
        )
        assert updated is refund
        assert refund.status == RefundStatus.COMPLETED
        assert refund.ledger_disbursement_posted is True
        ledger_mock.assert_awaited_once_with(db, refund)
        notify_mock.assert_awaited_once_with(refund)

    @pytest.mark.asyncio
    async def test_complete_manual_refund_requires_reference_and_is_idempotent(
        self, monkeypatch
    ):
        refund = SimpleNamespace(
            id=uuid.uuid4(),
            refund_reference="REF-MANUAL001",
            payment_reference="store-order-bank-1",
            source_service="store",
            source_entity_type="store_order_case",
            source_entity_id=str(uuid.uuid4()),
            purpose="store_order",
            currency="NGN",
            member_auth_id="user-1",
            cash_refund_kobo=2_000_000,
            bubbles_refund=0,
            store_credit_refund_kobo=0,
            provider="manual_transfer",
            provider_refund_id=None,
            external_bank_reference=None,
            status=RefundStatus.APPROVED,
            admin_note=None,
            completed_by_auth_id=None,
            completed_at=None,
            failure_reason=None,
            ledger_accrual_posted=True,
            ledger_disbursement_posted=False,
        )
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        ledger_mock = AsyncMock(return_value=True)
        notify_mock = AsyncMock()
        monkeypatch.setattr(
            refund_service, "emit_refund_completed_to_ledger", ledger_mock
        )
        monkeypatch.setattr(
            refund_service, "notify_source_service_refund_status", notify_mock
        )

        # Complete first time
        await refund_service.complete_manual_refund(
            db,
            refund,
            actor_auth_id="admin-1",
            external_bank_reference="STANBIC-NIP-998877",
        )
        assert refund.status == RefundStatus.COMPLETED
        assert refund.external_bank_reference == "STANBIC-NIP-998877"
        assert refund.ledger_disbursement_posted is True

        # Idempotent retry with same ref returns cleanly
        same = await refund_service.complete_manual_refund(
            db,
            refund,
            actor_auth_id="admin-1",
            external_bank_reference="STANBIC-NIP-998877",
        )
        assert same is refund

        # Different ref fails with 409
        with pytest.raises(HTTPException) as err:
            await refund_service.complete_manual_refund(
                db,
                refund,
                actor_auth_id="admin-1",
                external_bank_reference="OTHER-REF-000",
            )
        assert err.value.status_code == 409


class TestSettlementDeductionLedgerFix:
    @pytest.mark.asyncio
    async def test_settlement_with_paystack_refund_deduction_does_not_inflate_psp_fees(
        self, monkeypatch
    ):
        # ₦100,000 gross processed, ₦1,500 Paystack fees, ₦25,000 refund deducted -> ₦73,500 net to bank
        settlement = SimpleNamespace(
            paystack_settlement_id="SETTLE-20261125",
            gross_minor=10_000_000,
            fees_minor=150_000,
            deductions_minor=2_500_000,
            net_minor=7_350_000,
            currency="NGN",
            settlement_date=datetime(2026, 11, 25).date(),
        )
        posted_kwargs = {}

        async def fake_post(**kwargs):
            posted_kwargs.update(kwargs)

        monkeypatch.setattr(ledger_emit, "post_journal_entry", fake_post)
        db = SimpleNamespace()

        ok = await ledger_emit.emit_settlement_to_ledger(db, settlement)
        assert ok is True
        lines = {line["account_ref"]: line for line in posted_kwargs["lines"]}
        assert lines["bank_operating_ngn"]["debit"] == 7_350_000
        assert lines["expense_psp_fees"]["debit"] == 150_000  # Strictly fees, NOT 2,650,000!
        assert lines["paystack_clearing"]["credit"] == 7_500_000  # net + fees = gross - deductions
        assert (
            lines["bank_operating_ngn"]["debit"] + lines["expense_psp_fees"]["debit"]
            == lines["paystack_clearing"]["credit"]
        )

    @pytest.mark.asyncio
    async def test_two_step_refund_ledger_uses_refunds_payable_and_paystack_clearing(
        self, monkeypatch
    ):
        captured = []

        async def fake_post(**kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(ledger_emit, "post_journal_entry", fake_post)
        refund = SimpleNamespace(
            refund_reference="REF-LEDGER01",
            payment_reference="store-order-77",
            purpose=PaymentPurpose.STORE_ORDER.value,
            source_service="store",
            source_entity_id="order-77",
            member_auth_id="user-77",
            currency="NGN",
            cash_refund_kobo=2_500_000,
            provider="paystack",
            provider_refund_id="ps_77",
            external_bank_reference=None,
            approved_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db = SimpleNamespace()

        assert await ledger_emit.emit_refund_approved_to_ledger(db, refund) is True
        assert await ledger_emit.emit_refund_completed_to_ledger(db, refund) is True
        assert len(captured) == 2

        accrual = {line["account_ref"]: line for line in captured[0]["lines"]}
        assert accrual["revenue_store"]["debit"] == 2_500_000
        assert accrual["refunds_payable"]["credit"] == 2_500_000

        disbursement = {line["account_ref"]: line for line in captured[1]["lines"]}
        assert disbursement["refunds_payable"]["debit"] == 2_500_000
        assert disbursement["paystack_clearing"]["credit"] == 2_500_000

    def test_payments_app_mounts_shared_refund_routes(self):
        from services.payments_service.app.main import app

        paths = {route.path for route in app.routes}
        assert "/payments/admin/refunds" in paths
        assert "/payments/admin/refunds/{refund_id}/approve" in paths
        assert "/payments/admin/refunds/{refund_id}/initiate-paystack" in paths
        assert "/payments/admin/refunds/{refund_id}/sync-paystack" in paths
        assert "/payments/admin/refunds/{refund_id}/verify-bank-account" in paths
        assert "/payments/admin/refunds/{refund_id}/complete-manual" in paths
        assert "/payments/me/refunds" in paths
        assert "/internal/payments/refunds" in paths
        assert "/internal/payments/by-reference/{reference}/status" in paths
        assert "/internal/payments/by-reference/{reference}/close-unpaid" in paths
