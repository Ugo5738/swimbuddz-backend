"""Unit tests for the installment schedule builder and member-payment apply.

Covers:
  - Auto-computed installment count per program duration.
  - The 1/3 floor on custom deposit overrides (founder policy May 2026).
  - Member-initiated payment rolling across installments with custom amounts.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pytest

from services.academy_service.models import InstallmentStatus
from services.academy_service.services.installments import (
    apply_member_payment_across_installments,
    build_schedule,
    installment_count,
    split_amounts,
)


@dataclass
class FakeInstallment:
    """Lightweight stand-in for the SQLAlchemy model — same attributes."""

    installment_number: int
    amount: int
    status: InstallmentStatus = InstallmentStatus.PENDING
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None


class TestInstallmentCount:
    def test_8_week_program_yields_2_blocks(self):
        assert installment_count(total_fee=10_000_000, duration_weeks=8) == 2

    def test_12_week_program_yields_3_blocks(self):
        assert installment_count(total_fee=10_000_000, duration_weeks=12) == 3

    def test_over_threshold_caps_at_3(self):
        # Over NGN 150k → capped at 3 installments even with longer duration
        assert installment_count(total_fee=20_000_000, duration_weeks=16) == 3


class TestSplitAmounts:
    def test_even_split_no_remainder(self):
        assert split_amounts(15_000_000, 3) == [5_000_000, 5_000_000, 5_000_000]

    def test_remainder_goes_to_first(self):
        # 100 split 3 ways: 34, 33, 33 — first absorbs the +1.
        assert split_amounts(100, 3) == [34, 33, 33]


class TestBuildScheduleDepositFloor:
    """The 1/3 floor on custom deposit_override."""

    def test_deposit_below_third_raises(self):
        with pytest.raises(ValueError, match="less than 1/3"):
            build_schedule(
                total_fee=15_000_000,  # 150k
                duration_weeks=12,
                cohort_start=datetime(2026, 1, 5, tzinfo=timezone.utc),
                count_override=2,
                deposit_override=4_000_000,  # 40k = 26.7%, below 1/3
            )

    def test_deposit_at_third_succeeds(self):
        # Exactly 1/3 should pass (50k of 150k)
        schedule = build_schedule(
            total_fee=15_000_000,
            duration_weeks=12,
            cohort_start=datetime(2026, 1, 5, tzinfo=timezone.utc),
            count_override=2,
            deposit_override=5_000_000,
        )
        assert schedule[0]["amount"] == 5_000_000
        assert schedule[1]["amount"] == 10_000_000

    def test_deposit_above_third_succeeds(self):
        # The Asari case: 62.5k deposit + 87.5k = 150k
        schedule = build_schedule(
            total_fee=15_000_000,
            duration_weeks=12,
            cohort_start=datetime(2026, 1, 5, tzinfo=timezone.utc),
            count_override=2,
            deposit_override=6_250_000,
        )
        assert schedule[0]["amount"] == 6_250_000
        assert schedule[1]["amount"] == 8_750_000

    def test_no_deposit_override_no_floor_check(self):
        # Default even split: 50k/50k/50k for 150k 12-week — no override, no check.
        schedule = build_schedule(
            total_fee=15_000_000,
            duration_weeks=12,
            cohort_start=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )
        assert len(schedule) == 3
        assert all(s["amount"] == 5_000_000 for s in schedule)


class TestApplyMemberPayment:
    """Member-initiated payment rolling across installments."""

    def _make_installments(self, amounts: list[int]) -> list[FakeInstallment]:
        return [
            FakeInstallment(installment_number=i + 1, amount=a)
            for i, a in enumerate(amounts)
        ]

    def test_exact_next_installment_amount(self):
        installments = self._make_installments([50, 50, 50])
        now = datetime.now(timezone.utc)
        modified, overshoot = apply_member_payment_across_installments(
            amount_kobo=50,
            installments=installments,
            now=now,
            payment_reference="PAY-X",
        )
        assert len(modified) == 1
        assert modified[0].status == InstallmentStatus.PAID
        assert installments[1].status == InstallmentStatus.PENDING
        assert overshoot == 0

    def test_pays_full_remaining_balance_marks_all_paid(self):
        installments = self._make_installments([50, 50, 50])
        now = datetime.now(timezone.utc)
        modified, overshoot = apply_member_payment_across_installments(
            amount_kobo=150,
            installments=installments,
            now=now,
        )
        assert len(modified) == 3
        assert all(i.status == InstallmentStatus.PAID for i in installments)
        assert overshoot == 0

    def test_custom_amount_rolls_forward_with_reduction(self):
        # Member pays 75 across 50/50/50 — installment 1 paid in full,
        # installment 2 reduced to 25, installment 3 untouched.
        installments = self._make_installments([50, 50, 50])
        now = datetime.now(timezone.utc)
        modified, overshoot = apply_member_payment_across_installments(
            amount_kobo=75,
            installments=installments,
            now=now,
            payment_reference="PAY-Y",
        )
        assert installments[0].status == InstallmentStatus.PAID
        assert installments[0].payment_reference == "PAY-Y"
        assert installments[1].status == InstallmentStatus.PENDING
        assert installments[1].amount == 25
        assert installments[2].status == InstallmentStatus.PENDING
        assert installments[2].amount == 50
        assert len(modified) == 2
        assert overshoot == 0

    def test_skips_already_paid_installments(self):
        installments = self._make_installments([50, 50, 50])
        installments[0].status = InstallmentStatus.PAID
        now = datetime.now(timezone.utc)
        modified, overshoot = apply_member_payment_across_installments(
            amount_kobo=50,
            installments=installments,
            now=now,
        )
        # Should target installment 2, not re-pay installment 1
        assert installments[1].status == InstallmentStatus.PAID
        assert len(modified) == 1
        assert modified[0].installment_number == 2

    def test_overshoot_reported_when_amount_exceeds_balance(self):
        # Caller is expected to validate, but the helper returns overshoot
        # so caller can detect a validation bug.
        installments = self._make_installments([50, 50])
        now = datetime.now(timezone.utc)
        _, overshoot = apply_member_payment_across_installments(
            amount_kobo=200,
            installments=installments,
            now=now,
        )
        assert overshoot == 100
