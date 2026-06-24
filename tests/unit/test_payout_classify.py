"""Unit tests for the explicit-attendance payout classifier.

Validates the founder-confirmed May 2026 policy: the coach is paid for a
(student, session) pair iff an ``AttendanceRecord`` exists with status
``PRESENT`` or ``LATE``. Any other state (Absent, Excused, Cancelled, or
no record at all) does NOT credit the coach. Excused additionally creates
a make-up obligation downstream.
"""

from decimal import Decimal

import pytest

from services.payments_service.services.payout_calculator import (
    _paid_classes,
    _role_share,
    classify_session_for_payout,
)


class TestClassifySessionForPayout:
    @pytest.mark.parametrize(
        "status", ["present", "late", "PRESENT", "LATE", "Present", "Late"]
    )
    def test_present_and_late_count_as_delivered(self, status):
        # Case-insensitive: matches how attendance rows are stored in prod
        # (lowercase string enum) but tolerates legacy uppercase values too.
        assert classify_session_for_payout(status) == "delivered"

    def test_excused_classifies_as_excused(self):
        # Excused → no pay this session, BUT downstream a CohortMakeupObligation
        # is created so the coach is paid when the make-up is delivered.
        assert classify_session_for_payout("excused") == "excused"

    def test_absent_classifies_as_skip(self):
        # No-show. Under the new policy the coach is NOT paid (no lesson
        # actually held with the student). Under the old default-present
        # model this would have been counted as delivered.
        assert classify_session_for_payout("absent") == "skip"

    def test_cancelled_classifies_as_skip(self):
        # Session itself was cancelled at attendance level. Not delivered.
        assert classify_session_for_payout("cancelled") == "skip"

    def test_no_record_classifies_as_skip(self):
        # Critical regression boundary. The legacy code treated `None` as
        # delivered (default-present). The new code treats it as skip —
        # coaches must explicitly mark Present/Late to be credited.
        assert classify_session_for_payout(None) == "skip"

    def test_unknown_status_classifies_as_skip(self):
        # Defensive: any future/legacy/typo status falls into "skip" so
        # we never accidentally credit the coach for an unclassified state.
        assert classify_session_for_payout("walkin") == "skip"
        assert classify_session_for_payout("") == "skip"


class TestRoleShare:
    """Main/assistant 70-30 split, applied from the active roster at pay time."""

    def test_single_coach_gets_full_pay(self):
        assert _role_share(1, "lead") == Decimal("1")

    def test_zero_coaches_defaults_to_full(self):
        # Defensive: never silently zero a coach out.
        assert _role_share(0, "lead") == Decimal("1")

    def test_two_coaches_lead_gets_seventy(self):
        assert _role_share(2, "lead") == Decimal("0.70")

    def test_two_coaches_assistant_gets_thirty(self):
        assert _role_share(2, "assistant") == Decimal("0.30")

    def test_lead_plus_assistant_shares_sum_to_one(self):
        assert _role_share(2, "lead") + _role_share(2, "assistant") == Decimal("1.00")

    def test_three_or_more_still_splits_by_role(self):
        # >= 2 active coaches splits; lead still 70%.
        assert _role_share(3, "lead") == Decimal("0.70")


class TestPaidClasses:
    """Cumulative per-student cap: never pay beyond total_classes."""

    def test_under_cap_pays_all_delivered(self):
        assert _paid_classes(delivered_count=4, prior_delivered=0, class_cap=12) == 4

    def test_partial_remaining_caps_to_remaining(self):
        # 10 already paid in earlier blocks, 4 delivered now, cap 12 → only 2.
        assert _paid_classes(delivered_count=4, prior_delivered=10, class_cap=12) == 2

    def test_cap_reached_pays_nothing(self):
        assert _paid_classes(delivered_count=4, prior_delivered=12, class_cap=12) == 0

    def test_over_cap_clamps_to_zero(self):
        # Defensive: prior already exceeds cap (e.g. extra make-ups) → no pay.
        assert _paid_classes(delivered_count=4, prior_delivered=14, class_cap=12) == 0

    def test_no_attendance_pays_nothing(self):
        assert _paid_classes(delivered_count=0, prior_delivered=0, class_cap=12) == 0
