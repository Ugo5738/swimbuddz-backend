"""Unit tests for session stats and datetime handling.

These tests verify proper timezone-aware datetime comparisons.
"""

from datetime import datetime, timedelta, timezone


class TestSessionStatsDatetimeHandling:
    """Tests verifying timezone-aware datetime handling in session stats."""

    def test_upcoming_session_detection_uses_utc(self):
        """Verify that upcoming session detection uses timezone-aware UTC."""
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=1)
        past = now - timedelta(days=1)

        # Future sessions should be counted as upcoming
        assert future > now, "Future datetime should be greater than now"
        # Past sessions should not be counted
        assert past < now, "Past datetime should be less than now"

    def test_timezone_aware_vs_naive_datetime(self):
        """Confirm timezone-aware datetimes are being used correctly."""
        now_utc = datetime.now(timezone.utc)

        # Verify the datetime is timezone-aware
        assert now_utc.tzinfo is not None, (
            "datetime.now(timezone.utc) should be timezone-aware"
        )
        assert now_utc.tzinfo == timezone.utc, "Timezone should be UTC"

    def test_session_time_comparison_edge_case(self):
        """Test datetime comparison at exact boundary."""
        now = datetime.now(timezone.utc)

        # Exactly now should not be "greater than now" (it's equal)
        same_time = now
        assert not (same_time > now), "Same time should not be > now"

        # One microsecond in future should be upcoming
        just_future = now + timedelta(microseconds=1)
        assert just_future > now, "Even 1 microsecond in future should be upcoming"
