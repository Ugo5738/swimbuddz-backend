"""Quarterly report email templates."""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    cta_button,
    detail_box,
    sign_off,
    wrap_html,
)


async def send_quarterly_report_email(
    to_email: str,
    member_name: str,
    year: int,
    quarter: int,
    sessions_attended: int = 0,
    attendance_rate: float = 0.0,
    streak_longest: int = 0,
    milestones_achieved: int = 0,
    bubbles_earned: int = 0,
    volunteer_hours: float = 0.0,
    total_spent_ngn: int = 0,
    report_url: str = "",
) -> bool:
    """Send quarterly report notification email to a member."""
    label = f"Q{quarter} {year}"
    att_pct = f"{attendance_rate * 100:.0f}%"

    subject = f"Your {label} SwimBuddz Report is Ready"

    # ── Plain text fallback ──
    body = (
        f"Hi {member_name},\n\n"
        f"Your {label} Quarterly Report is ready!\n\n"
        f"Here's a quick summary:\n"
        f"  Sessions Attended: {sessions_attended}\n"
        f"  Attendance Rate: {att_pct}\n"
        f"  Longest Streak: {streak_longest} weeks\n"
        f"  Milestones: {milestones_achieved}\n"
        f"  Bubbles Earned: {bubbles_earned}\n\n"
        f"View your full report and download your shareable card:\n"
        f"{report_url}\n\n"
        f"Keep swimming!\n"
        f"-- The SwimBuddz Team"
    )

    # ── HTML version ──
    stats = {
        "Sessions Attended": str(sessions_attended),
        "Attendance Rate": att_pct,
        "Longest Streak": f"{streak_longest} weeks",
        "Milestones Achieved": str(milestones_achieved),
        "Bubbles Earned": str(bubbles_earned),
    }
    if volunteer_hours > 0:
        stats["Volunteer Hours"] = f"{volunteer_hours:.1f}"
    if total_spent_ngn > 0:
        stats["Total Spent"] = f"NGN {total_spent_ngn:,}"

    inner_html = (
        f"<p>Hi <strong>{member_name}</strong>,</p>"
        f"<p>Your <strong>{label} Quarterly Report</strong> is ready! "
        f"Here's a snapshot of your swimming journey this quarter:</p>"
        + detail_box(stats)
        + "<p>View your full report, check the community leaderboard, "
        "and download your personalised SwimBuddz Wrapped card to share "
        "with friends.</p>" + cta_button("View My Report", report_url) + sign_off()
    )

    html_body = wrap_html(
        title=f"Your {label} Report",
        subtitle="Here's what you achieved this quarter",
        body_html=inner_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"You attended {sessions_attended} sessions with {att_pct} attendance this quarter!",
    )

    return await send_email(to_email, subject, body, html_body)
