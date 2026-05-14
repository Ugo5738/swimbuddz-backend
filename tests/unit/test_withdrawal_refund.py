"""Unit tests for the withdrawal-refund policy computation.

Policy (founder-confirmed May 2026):
  - Before cohort starts: 90% refund of paid amount.
  - Inside the mid-entry window (week 1..mid_entry_cutoff_week): 50% of the
    unused-by-time portion of the program fee, capped at what was paid.
  - After the cutoff: no refund.
"""

from datetime import datetime, timedelta, timezone

from services.academy_service.services.installments import (
    compute_withdrawal_refund,
)


class TestComputeWithdrawalRefund:
    def test_before_cohort_starts_returns_90_percent(self):
        cohort_start = datetime.now(timezone.utc) + timedelta(days=7)
        window, refund, percent = compute_withdrawal_refund(
            now=datetime.now(timezone.utc),
            cohort_start=cohort_start,
            duration_weeks=12,
            mid_entry_cutoff_week=5,
            total_paid_kobo=15_000_000,  # 150k full pay
            program_fee_kobo=15_000_000,
        )
        assert window == "before_start"
        assert refund == 13_500_000  # 90% of 150k
        assert percent == 0.9

    def test_mid_entry_window_uses_50_percent_of_unused(self):
        # 12-week cohort, member at week 4 (28 days elapsed = 4 weeks),
        # paid 1 installment (50k of 150k). Unused = 150k * (8/12) = 100k.
        # 50% unused = 50k, capped at paid (50k) = 50k.
        cohort_start = datetime.now(timezone.utc) - timedelta(days=28)
        window, refund, _ = compute_withdrawal_refund(
            now=datetime.now(timezone.utc),
            cohort_start=cohort_start,
            duration_weeks=12,
            mid_entry_cutoff_week=5,
            total_paid_kobo=5_000_000,
            program_fee_kobo=15_000_000,
        )
        assert window == "mid_entry_window"
        assert refund == 5_000_000  # capped at paid (the 50% unused exceeds it)

    def test_mid_entry_window_caps_at_paid_amount(self):
        # Paid only 30k. 50% of unused (~67k for 12wk at week 4) > paid → cap.
        cohort_start = datetime.now(timezone.utc) - timedelta(days=14)
        window, refund, _ = compute_withdrawal_refund(
            now=datetime.now(timezone.utc),
            cohort_start=cohort_start,
            duration_weeks=12,
            mid_entry_cutoff_week=5,
            total_paid_kobo=3_000_000,
            program_fee_kobo=15_000_000,
        )
        assert window == "mid_entry_window"
        assert refund == 3_000_000

    def test_after_cutoff_returns_zero(self):
        cohort_start = datetime.now(timezone.utc) - timedelta(weeks=8)
        window, refund, percent = compute_withdrawal_refund(
            now=datetime.now(timezone.utc),
            cohort_start=cohort_start,
            duration_weeks=12,
            mid_entry_cutoff_week=5,
            total_paid_kobo=10_000_000,
            program_fee_kobo=15_000_000,
        )
        assert window == "after_cutoff"
        assert refund == 0
        assert percent == 0.0

    def test_zero_paid_returns_zero_regardless_of_window(self):
        cohort_start = datetime.now(timezone.utc) + timedelta(days=7)
        _, refund, _ = compute_withdrawal_refund(
            now=datetime.now(timezone.utc),
            cohort_start=cohort_start,
            duration_weeks=12,
            mid_entry_cutoff_week=5,
            total_paid_kobo=0,
            program_fee_kobo=15_000_000,
        )
        assert refund == 0
