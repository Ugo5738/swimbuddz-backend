"""
Session notification email templates.

Templates for session announcements, reminders, and updates.
"""

from libs.common.config import get_settings
from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    GRADIENT_AMBER,
    cta_button,
    detail_box,
    info_box,
    sign_off,
    wrap_html,
)

settings = get_settings()


async def send_session_announcement_email(
    to_email: str,
    member_name: str,
    session_title: str,
    session_type: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str = "",
    pool_fee: float = 0,
    is_short_notice: bool = False,
    short_notice_message: str = "",
    currency: str = "NGN",
) -> bool:
    """
    Send session announcement email when a new session is published.

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_title: Title of the session.
        session_type: Type of session (community, club, event).
        session_date: Formatted date string.
        session_time: Formatted time string.
        session_location: Pool/venue name.
        session_address: Optional full address.
        pool_fee: Session fee amount.
        is_short_notice: Whether this is a same-day/short notice session.
        short_notice_message: Optional message explaining the short notice.
        currency: Currency code for fee display.
    """
    fee_display = (
        f"₦{pool_fee:,.0f}" if currency == "NGN" else f"{currency} {pool_fee:,.2f}"
    )
    fee_text = f"Fee: {fee_display}" if pool_fee > 0 else "Fee: Free"

    # Header based on session type
    type_labels = {
        "community": "Community Swim",
        "club": "Club Session",
        "event": "Event",
        "cohort_class": "Academy Class",
    }
    type_label = type_labels.get(session_type.lower(), "Session")

    # Short notice banner
    short_notice_html = ""
    if is_short_notice:
        notice_msg = (
            short_notice_message or "This session was scheduled on short notice."
        )
        short_notice_html = info_box(
            f"⚠️ <strong>Short Notice</strong><br/>{notice_msg}",
            bg_color="#fef3c7",
            border_color="#f59e0b",
        )

    subject = f"New {type_label}: {session_title} on {session_date}"

    # Plain text body
    body = f"""Hi {member_name},

A new {type_label.lower()} has been scheduled!

{session_title}
📅 {session_date}
⏰ {session_time}
📍 {session_location}
{f"🗺️ {session_address}" if session_address else ""}
💳 {fee_text}

{"⚠️ " + short_notice_message if is_short_notice else ""}

View session details and register on the SwimBuddz app.

— The SwimBuddz Team
"""

    # HTML body
    details = {
        "📅 Date": session_date,
        "⏰ Time": session_time,
        "📍 Location": session_location,
    }
    if session_address:
        details["🗺️ Address"] = session_address
    if pool_fee > 0:
        details["💳 Fee"] = fee_display
    else:
        details["💳 Fee"] = "Free"

    frontend_url = settings.FRONTEND_URL

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>A new <strong>{type_label.lower()}</strong> has been scheduled!</p>"
        + short_notice_html
        + f"<h3>🏊‍♂️ {session_title}</h3>"
        + detail_box(details)
        + cta_button("View Session", f"{frontend_url}/sessions")
        + sign_off("Hope to see you there! 🌊")
    )

    html_body = wrap_html(
        title=f"🆕 New {type_label}",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"New {type_label.lower()} on {session_date}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_session_reminder_email(
    to_email: str,
    member_name: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str = "",
    reminder_type: str = "24h",
    pool_fee: float = 0,
    currency: str = "NGN",
) -> bool:
    """
    Send session reminder email (24h, 3h, or 1h before).

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_title: Title of the session.
        session_date: Formatted date string.
        session_time: Formatted time string.
        session_location: Pool/venue name.
        session_address: Optional full address.
        reminder_type: "24h", "3h", or "1h".
        pool_fee: Session fee amount.
        currency: Currency code for fee display.
    """
    reminder_messages = {
        "24h": ("Tomorrow", "Your session is tomorrow! Time to prepare."),
        "3h": ("Starting Soon", "Your session starts in a few hours. Get ready!"),
        "1h": ("Starting in 1 Hour", "Your session is about to begin!"),
    }
    title_suffix, intro_message = reminder_messages.get(
        reminder_type, ("Reminder", "Your session is coming up.")
    )

    subject = f"Reminder: {session_title} - {title_suffix}"

    # Plain text body
    body = f"""Hi {member_name},

{intro_message}

{session_title}
📅 {session_date}
⏰ {session_time}
📍 {session_location}
{f"🗺️ {session_address}" if session_address else ""}

What to bring:
✓ Swimwear and swim cap
✓ Goggles
✓ Towel
✓ Water bottle

See you there! 🏊‍♂️

— The SwimBuddz Team
"""

    # HTML body
    details = {
        "📅 Date": session_date,
        "⏰ Time": session_time,
        "📍 Location": session_location,
    }
    if session_address:
        details["🗺️ Address"] = session_address

    frontend_url = settings.FRONTEND_URL

    checklist_html = """
    <div style="background: #fefce8; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <h4 style="margin: 0 0 10px 0; color: #854d0e;">🎒 What to Bring</h4>
        <ul style="margin: 0; padding-left: 20px; color: #713f12;">
            <li>Swimwear and swim cap</li>
            <li>Goggles</li>
            <li>Towel</li>
            <li>Water bottle</li>
        </ul>
    </div>
    """

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>{intro_message}</p>"
        + f"<h3>🏊‍♂️ {session_title}</h3>"
        + detail_box(details)
        + checklist_html
        + cta_button("View Session", f"{frontend_url}/sessions")
        + sign_off("See you in the water! 🌊")
    )

    html_body = wrap_html(
        title=f"⏰ {title_suffix}",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Reminder: {session_title} - {title_suffix}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_session_cancelled_email(
    to_email: str,
    member_name: str,
    session_title: str,
    session_date: str,
    session_time: str,
    cancellation_reason: str = "",
) -> bool:
    """
    Send session cancellation notification.

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_title: Title of the session.
        session_date: Formatted date string.
        session_time: Formatted time string.
        cancellation_reason: Optional reason for cancellation.
    """
    subject = f"Session Cancelled: {session_title} on {session_date}"

    reason_text = f"\n\nReason: {cancellation_reason}" if cancellation_reason else ""

    body = f"""Hi {member_name},

We regret to inform you that the following session has been cancelled:

{session_title}
📅 {session_date}
⏰ {session_time}
{reason_text}

If you had already paid for this session, a refund will be processed automatically.

We apologize for any inconvenience. Please check the app for other upcoming sessions.

— The SwimBuddz Team
"""

    reason_html = ""
    if cancellation_reason:
        reason_html = info_box(
            f"<strong>Reason:</strong> {cancellation_reason}",
            bg_color="#fef2f2",
            border_color="#ef4444",
        )

    frontend_url = settings.FRONTEND_URL

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>We regret to inform you that the following session has been <strong>cancelled</strong>:</p>"
        + f"<h3 style='color: #ef4444;'>❌ {session_title}</h3>"
        + detail_box(
            {
                "📅 Date": session_date,
                "⏰ Time": session_time,
            },
            accent_color="#ef4444",
        )
        + reason_html
        + "<p>If you had already paid for this session, a refund will be processed automatically.</p>"
        + cta_button("View Other Sessions", f"{frontend_url}/sessions")
        + sign_off("We apologize for any inconvenience.")
    )

    html_body = wrap_html(
        title="❌ Session Cancelled",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"Session cancelled: {session_title}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_session_updated_email(
    to_email: str,
    member_name: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    changes_summary: str,
) -> bool:
    """
    Send session update notification (time/location changed).

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_title: Title of the session.
        session_date: New/current date.
        session_time: New/current time.
        session_location: New/current location.
        changes_summary: Description of what changed.
    """
    subject = f"Session Updated: {session_title}"

    body = f"""Hi {member_name},

The following session has been updated:

{session_title}

What changed:
{changes_summary}

Updated Details:
📅 {session_date}
⏰ {session_time}
📍 {session_location}

Please make note of the updated details.

— The SwimBuddz Team
"""

    frontend_url = settings.FRONTEND_URL

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>The following session has been <strong>updated</strong>:</p>"
        + f"<h3>📝 {session_title}</h3>"
        + info_box(
            f"<strong>What changed:</strong><br/>{changes_summary}",
            bg_color="#dbeafe",
            border_color="#3b82f6",
        )
        + "<h4>Updated Details:</h4>"
        + detail_box(
            {
                "📅 Date": session_date,
                "⏰ Time": session_time,
                "📍 Location": session_location,
            }
        )
        + cta_button("View Session", f"{frontend_url}/sessions")
        + sign_off("Please make note of the updated details.")
    )

    html_body = wrap_html(
        title="📝 Session Updated",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Session updated: {session_title}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_weekly_session_digest_email(
    to_email: str,
    member_name: str,
    week_label: str,
    sessions: list[dict],
) -> bool:
    """
    Send weekly digest of upcoming sessions.

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        week_label: e.g., "February 10-16, 2026"
        sessions: List of session dicts with keys:
            - title, date, time, location, type
    """
    if not sessions:
        # No sessions to report, skip sending
        return True

    subject = f"This Week's Sessions - {week_label}"

    # Plain text
    session_list = "\n".join(
        f"• {s['title']} - {s['date']} at {s['time']} ({s['location']})"
        for s in sessions
    )

    body = f"""Hi {member_name},

Here are this week's swimming sessions:

{session_list}

View the full schedule and register on the SwimBuddz app.

— The SwimBuddz Team
"""

    # HTML
    session_cards = ""
    for s in sessions:
        type_colors = {
            "community": "#0891b2",
            "club": "#8b5cf6",
            "event": "#f59e0b",
        }
        color = type_colors.get(s.get("type", "community").lower(), "#0891b2")
        session_cards += f"""
        <div style="background: #f8fafc; border-left: 4px solid {color}; 
                    border-radius: 0 8px 8px 0; padding: 16px 20px; margin: 12px 0;">
            <strong style="color: #1e293b;">{s['title']}</strong><br/>
            <span style="font-size: 14px; color: #64748b;">
                📅 {s['date']} &nbsp;•&nbsp; ⏰ {s['time']}<br/>
                📍 {s['location']}
            </span>
        </div>
        """

    frontend_url = settings.FRONTEND_URL

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>Here's what's happening this week at SwimBuddz:</p>"
        + session_cards
        + cta_button("View Full Schedule", f"{frontend_url}/sessions")
        + sign_off("See you in the water! 🌊")
    )

    html_body = wrap_html(
        title="📅 Weekly Session Digest",
        subtitle=week_label,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"This week's swimming sessions - {week_label}",
    )

    return await send_email(to_email, subject, body, html_body)
