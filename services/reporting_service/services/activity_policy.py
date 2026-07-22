"""Shared participation policy for personal reports and leaderboards."""

from typing import Any, Literal

MIN_LEADERBOARD_ATTENDANCE = 3
ReportActivityState = Literal["active", "low_activity", "no_activity"]


def meaningful_activity_count(report: Any) -> int:
    """Count recorded participation signals without treating spend as activity."""
    return (
        int(report.total_sessions_attended or 0)
        + int(report.events_attended or 0)
        + int(report.milestones_achieved or 0)
        + int(report.certificates_earned or 0)
        + (1 if float(report.volunteer_hours or 0) > 0 else 0)
    )


def report_activity_state(report: Any) -> ReportActivityState:
    count = meaningful_activity_count(report)
    if count == 0:
        return "no_activity"
    if count <= 2:
        return "low_activity"
    return "active"


def share_card_eligible(report: Any) -> bool:
    return meaningful_activity_count(report) > 0


def leaderboard_eligible(report: Any) -> bool:
    return int(report.total_sessions_attended or 0) >= MIN_LEADERBOARD_ATTENDANCE
