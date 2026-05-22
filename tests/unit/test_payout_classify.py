"""Unit tests for the explicit-attendance payout classifier.

Validates the founder-confirmed May 2026 policy: the coach is paid for a
(student, session) pair iff an ``AttendanceRecord`` exists with status
``PRESENT`` or ``LATE``. Any other state (Absent, Excused, Cancelled, or
no record at all) does NOT credit the coach. Excused additionally creates
a make-up obligation downstream.
"""

import pytest

from services.payments_service.services.payout_calculator import (
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
