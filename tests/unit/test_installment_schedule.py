"""Unit tests for the installment schedule builder and member-payment apply.

Covers:
  - Auto-computed installment count per program duration.
  - The 1/3 floor on custom deposit overrides (founder policy May 2026).
  - Member-initiated payment rolling across installments with custom amounts.
  - Mid-cohort enrollment date anchoring (May 2026 structural fix).
  - Due-date-based compliance + live missed_installments_count (May 2026).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from services.academy_service.models import (
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
)
from services.academy_service.services.installments import (
    apply_member_payment_across_installments,
    build_schedule,
    installment_count,
    split_amounts,
    sync_enrollment_installment_state,
)


@dataclass
class FakeInstallment:
    """Lightweight stand-in for the SQLAlchemy model — same attributes."""

    installment_number: int
    amount: int
    status: InstallmentStatus = InstallmentStatus.PENDING
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None


@dataclass
class FakeEnrollment:
    """Lightweight stand-in for Enrollment used by sync_enrollment_installment_state."""

    status: EnrollmentStatus = EnrollmentStatus.PENDING_APPROVAL
    payment_status: PaymentStatus = PaymentStatus.PENDING
    access_suspended: bool = False
    missed_installments_count: int = 0
    paid_installments_count: int = 0
    total_installments: int = 0
    dropped_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None


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


# =============================================================================
# Mid-cohort enrollment anchoring (May 2026 structural fix)
# =============================================================================


class TestBuildScheduleMidCohortAnchoring:
    """A student enrolling mid-cohort shouldn't get back-dated installments.

    Due dates are anchored to Monday 00:00 WAT (Africa/Lagos, UTC+1), so the
    UTC representation lands at Sunday 23:00 UTC for that Monday. Assertions
    here compare to the expected Monday in WAT to keep them readable.
    """

    COHORT_START = datetime(2026, 4, 18, tzinfo=timezone.utc)  # Saturday, Apr 18
    DURATION = 12

    @staticmethod
    def _wat_monday(year: int, month: int, day: int) -> datetime:
        """Return the UTC instant corresponding to 00:00 WAT on a given date."""
        from zoneinfo import ZoneInfo as _Z

        return datetime(year, month, day, 0, 0, tzinfo=_Z("Africa/Lagos")).astimezone(
            timezone.utc
        )

    def _schedule(self, enrolled_at: Optional[datetime]) -> list[dict]:
        return build_schedule(
            total_fee=15_000_000,  # ₦150,000
            duration_weeks=self.DURATION,
            cohort_start=self.COHORT_START,
            enrolled_at=enrolled_at,
        )

    def test_no_enrolled_at_anchors_to_cohort_start(self):
        # Back-compat: callers that don't pass enrolled_at get the legacy
        # cohort-anchored schedule. Cohort start Apr 18 (Sat) → Monday anchor
        # Apr 13.
        schedule = self._schedule(enrolled_at=None)
        assert schedule[0]["due_at"] == self._wat_monday(2026, 4, 13)

    def test_enrollment_before_cohort_starts_uses_cohort_anchor(self):
        # Member who signs up two weeks early: schedule still anchors to
        # cohort start, not enrollment date (we don't accelerate billing).
        schedule = self._schedule(enrolled_at=datetime(2026, 4, 4, tzinfo=timezone.utc))
        assert schedule[0]["due_at"] == self._wat_monday(2026, 4, 13)

    def test_enrollment_at_cohort_start_uses_cohort_anchor(self):
        schedule = self._schedule(
            enrolled_at=datetime(2026, 4, 18, tzinfo=timezone.utc)
        )
        assert schedule[0]["due_at"] == self._wat_monday(2026, 4, 13)

    def test_week_3_joiner_anchors_to_enrollment_monday(self):
        # Member joining mid-Week 3 — installment 1 due that week's Monday,
        # not back-dated to cohort start (Week 1).
        enrolled = datetime(2026, 5, 2, 9, 0, tzinfo=timezone.utc)  # Sat W3
        schedule = self._schedule(enrolled_at=enrolled)
        assert schedule[0]["due_at"] == self._wat_monday(2026, 4, 27)
        assert schedule[1]["due_at"] == self._wat_monday(2026, 5, 25)
        assert schedule[2]["due_at"] == self._wat_monday(2026, 6, 22)

    def test_winifred_scenario_no_due_at_in_the_past(self):
        # Real case from May 2026: Winifred enrolled Wed May 20 (Week 5).
        # With back-dating, inst 1 was due Apr 12 and inst 2 was due May 10 —
        # both already past at enrollment time, triggering immediate
        # DROPOUT_PENDING. With the fix, no due date is in the past at
        # enrollment.
        enrolled = datetime(2026, 5, 20, 7, 45, tzinfo=timezone.utc)
        schedule = self._schedule(enrolled_at=enrolled)
        for item in schedule:
            assert item["due_at"] >= enrolled - timedelta(days=7), (
                f"Installment {item['installment_number']} due_at "
                f"{item['due_at']} is older than the enrollment week"
            )


# =============================================================================
# Compliance sync — due-date-based + live missed counter (May 2026 fix)
# =============================================================================


class TestSyncEnrollmentInstallmentState:
    """The compliance rules that drive suspension and dropout."""

    COHORT_START = datetime(2026, 4, 18, tzinfo=timezone.utc)
    DURATION = 12

    def _sync(
        self,
        enrollment: FakeEnrollment,
        installments: list,
        *,
        now: datetime,
        admin_dropout_approval: bool = False,
        cohort_requires_approval: bool = False,
    ) -> None:
        sync_enrollment_installment_state(
            enrollment=enrollment,
            installments=installments,
            duration_weeks=self.DURATION,
            cohort_start=self.COHORT_START,
            cohort_requires_approval=cohort_requires_approval,
            admin_dropout_approval=admin_dropout_approval,
            now=now,
        )

    def test_future_due_date_does_not_suspend(self):
        # Asari's case: 2 installments, inst 1 PAID, inst 2 due in 3 weeks.
        # System must NOT suspend just because the cohort calendar's block 2
        # has started.
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)  # Week 6 of cohort
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=6_250_000,
                status=InstallmentStatus.PAID,
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=8_750_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 6, 11, tzinfo=timezone.utc),  # 3w future
            ),
        ]
        enrollment = FakeEnrollment(status=EnrollmentStatus.ENROLLED)
        self._sync(enrollment, installments, now=now)
        assert enrollment.access_suspended is False
        assert enrollment.payment_status == PaymentStatus.PAID

    def test_past_due_unpaid_suspends(self):
        # Inst 2 due 5 days ago, still PENDING (not yet marked MISSED by cron).
        # The compliance check should still trigger suspension on the basis
        # of due_at + grace, not on installment status alone.
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.PAID,
                due_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 5, 17, tzinfo=timezone.utc),  # 5d ago
            ),
        ]
        enrollment = FakeEnrollment(status=EnrollmentStatus.ENROLLED)
        self._sync(enrollment, installments, now=now)
        assert enrollment.access_suspended is True
        assert enrollment.payment_status == PaymentStatus.FAILED

    def test_within_grace_window_does_not_suspend(self):
        # Inst 2 due 12 hours ago — still within the 24h grace window.
        now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.PAID,
                due_at=datetime(2026, 4, 27, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        enrollment = FakeEnrollment(status=EnrollmentStatus.ENROLLED)
        self._sync(enrollment, installments, now=now)
        assert enrollment.access_suspended is False

    def test_two_misses_trigger_dropout_pending_when_admin_approval_required(self):
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.MISSED,
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.MISSED,
                due_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=3,
                amount=5_000_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 6, 7, tzinfo=timezone.utc),
            ),
        ]
        enrollment = FakeEnrollment(status=EnrollmentStatus.ENROLLED)
        self._sync(enrollment, installments, now=now, admin_dropout_approval=True)
        assert enrollment.status == EnrollmentStatus.DROPOUT_PENDING
        assert enrollment.dropped_at == now
        assert enrollment.access_suspended is True
        assert enrollment.missed_installments_count == 2

    def test_two_misses_trigger_dropped_when_no_admin_approval(self):
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.MISSED,
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.MISSED,
                due_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
            ),
        ]
        enrollment = FakeEnrollment(status=EnrollmentStatus.ENROLLED)
        self._sync(enrollment, installments, now=now, admin_dropout_approval=False)
        assert enrollment.status == EnrollmentStatus.DROPPED

    def test_paying_a_missed_installment_decreases_live_counter(self):
        # Counter is live (not cumulative behavioral): paying a previously
        # MISSED installment flips its status to PAID and missed_count drops.
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.PAID,  # paid the late one
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.MISSED,
                due_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
            ),
        ]
        # Counter was previously 2 (cumulative behavioral history).
        enrollment = FakeEnrollment(
            status=EnrollmentStatus.ENROLLED,
            missed_installments_count=2,
        )
        self._sync(enrollment, installments, now=now)
        # Live count: only 1 actually MISSED right now.
        assert enrollment.missed_installments_count == 1

    def test_dropout_pending_reverts_when_student_catches_up(self):
        # Winifred's scenario after she pays: was DROPOUT_PENDING with
        # missed_count=2, then paid installment 1 which flipped MISSED→PAID,
        # so missed_count is now <2 → status should auto-revert to ENROLLED.
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.PAID,
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
            FakeInstallment(
                installment_number=2,
                amount=5_000_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 6, 17, tzinfo=timezone.utc),  # future
            ),
            FakeInstallment(
                installment_number=3,
                amount=5_000_000,
                status=InstallmentStatus.PENDING,
                due_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
            ),
        ]
        enrollment = FakeEnrollment(
            status=EnrollmentStatus.DROPOUT_PENDING,
            missed_installments_count=2,
            dropped_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
        self._sync(enrollment, installments, now=now, admin_dropout_approval=True)
        assert enrollment.status == EnrollmentStatus.ENROLLED
        assert enrollment.dropped_at is None
        assert enrollment.access_suspended is False
        assert enrollment.missed_installments_count == 0

    def test_dropped_state_is_not_auto_reverted(self):
        # DROPPED is final — admin must reverse it explicitly. Even if the
        # student magically had no misses, the status stays DROPPED.
        now = datetime(2026, 5, 22, tzinfo=timezone.utc)
        installments = [
            FakeInstallment(
                installment_number=1,
                amount=5_000_000,
                status=InstallmentStatus.PAID,
                due_at=datetime(2026, 4, 13, tzinfo=timezone.utc),
            ),
        ]
        enrollment = FakeEnrollment(
            status=EnrollmentStatus.DROPPED,
            missed_installments_count=2,
            dropped_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )
        self._sync(enrollment, installments, now=now)
        assert enrollment.status == EnrollmentStatus.DROPPED
        assert enrollment.dropped_at == datetime(2026, 5, 20, tzinfo=timezone.utc)
