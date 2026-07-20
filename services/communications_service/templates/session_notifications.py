"""
Session notification email templates.

Templates for session booking prompts, reminders, and updates.
"""

from html import escape

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


def _weather_text(weather_summary: dict | None) -> str:
    if not weather_summary:
        return ""

    parts = [str(weather_summary.get("condition_text") or "")]
    if weather_summary.get("temperature_text"):
        parts.append(str(weather_summary["temperature_text"]))
    if weather_summary.get("rain_chance_text"):
        parts.append(str(weather_summary["rain_chance_text"]))
    if weather_summary.get("rainfall_text"):
        parts.append(str(weather_summary["rainfall_text"]))

    summary = " · ".join(part for part in parts if part)
    explanation = str(weather_summary.get("explanation") or "").strip()
    explanation_line = f"\n{explanation}" if explanation else ""
    return f"\nWeather:\n{summary}{explanation_line}\n"


def _weather_html(weather_summary: dict | None) -> str:
    if not weather_summary:
        return ""

    parts = [str(weather_summary.get("condition_text") or "")]
    if weather_summary.get("temperature_text"):
        parts.append(str(weather_summary["temperature_text"]))
    if weather_summary.get("rain_chance_text"):
        parts.append(str(weather_summary["rain_chance_text"]))
    if weather_summary.get("rainfall_text"):
        parts.append(str(weather_summary["rainfall_text"]))

    summary = " · ".join(escape(part) for part in parts if part)
    explanation = escape(str(weather_summary.get("explanation") or "").strip())
    explanation_html = (
        f'<br/><span style="font-size: 13px; color: #475569;">{explanation}</span>'
        if explanation
        else ""
    )
    return info_box(
        f"<strong>Weather</strong><br/>{summary}{explanation_html}",
        bg_color="#f0f9ff",
        border_color="#0284c7",
    )


async def send_session_announcement_email(
    to_email: str,
    member_name: str,
    session_id: str,
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
    weather_summary: dict | None = None,
) -> bool:
    """
    Send a booking prompt when a session is available to book.

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_id: UUID of the session being booked.
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
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    booking_url = f"{frontend_url}/sessions/{session_id}/book"

    # Short notice banner
    short_notice_html = ""
    short_notice_text = (
        short_notice_message or "This session was scheduled on short notice."
    )
    if is_short_notice:
        short_notice_html = info_box(
            f"⚠️ <strong>Short Notice</strong><br/>{short_notice_text}",
            bg_color="#fef3c7",
            border_color="#f59e0b",
        )

    subject = f"New {type_label}: {session_title} on {session_date}"
    weather_text = _weather_text(weather_summary)
    weather_html = _weather_html(weather_summary)

    # Plain text body
    body = f"""Hi {member_name},

A new {type_label.lower()} is available to book.

{session_title}
📅 {session_date}
⏰ {session_time}
📍 {session_location}
{f"🗺️ {session_address}" if session_address else ""}
💳 {fee_text}
{weather_text}

{f"⚠️ {short_notice_text}" if is_short_notice else ""}

What to bring:
✓ Swimwear and swim cap
✓ Goggles
✓ Towel
✓ Water bottle

Book your spot here:
{booking_url}

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
        f"<p>A new <strong>{type_label.lower()}</strong> is available to book.</p>"
        + short_notice_html
        + f"<h3>🏊‍♂️ {session_title}</h3>"
        + detail_box(details)
        + weather_html
        + checklist_html
        + cta_button("Book Session", booking_url)
        + sign_off("Book early so your spot is held. 🌊")
    )

    html_body = wrap_html(
        title=f"New {type_label}",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"New {type_label.lower()} on {session_date}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_session_prospect_invite_email(
    to_email: str,
    member_name: str,
    session_id: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str = "",
    pool_fee: float = 0,
    currency: str = "NGN",
    weather_summary: dict | None = None,
) -> bool:
    """Invite an unpaid signup to choose a SwimBuddz path before booking."""
    fee_display = (
        f"₦{pool_fee:,.0f}" if currency == "NGN" else f"{currency} {pool_fee:,.2f}"
    )
    fee_text = f"Pool fee: {fee_display}" if pool_fee > 0 else "Pool fee: Free"
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    activation_url = f"{frontend_url}/account/billing?required=community"
    booking_url = f"{frontend_url}/sessions/{session_id}/book"
    weather_text = _weather_text(weather_summary)
    weather_html = _weather_html(weather_summary)

    subject = f"Choose your SwimBuddz path - Community swim on {session_date}"

    body = f"""Hi {member_name},

We have a Community swim coming up, and it is a good moment to choose how you want to join SwimBuddz.

{session_title}
📅 {session_date}
⏰ {session_time}
📍 {session_location}
{f"🗺️ {session_address}" if session_address else ""}
💳 {fee_text}
{weather_text}

Choose the path that fits you:
- Community: open swims, events, and the broader SwimBuddz network.
- Club: weekly structured training with a crew.
- Academy: cohort-based programs for learning or improving your swimming.

Choose and activate your membership here:
{activation_url}

Booking is reserved for active SwimBuddz members. After activation, you can book this session here:
{booking_url}

If you are new and want to ask about a first-timer visit before paying, reply to this email and we will confirm what is possible for that session.

— The SwimBuddz Team
"""

    details = {
        "📅 Date": session_date,
        "⏰ Time": session_time,
        "📍 Location": session_location,
        "💳 Pool fee": fee_display if pool_fee > 0 else "Free",
    }
    if session_address:
        details["🗺️ Address"] = session_address

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>We have a <strong>Community swim</strong> coming up and thought you "
        "might want to choose how you want to join SwimBuddz.</p>"
        + f"<h3>🏊‍♂️ {session_title}</h3>"
        + detail_box(details)
        + weather_html
        + info_box(
            "<strong>Choose your path</strong><br/>"
            "Community: open swims, events, and the broader network.<br/>"
            "Club: weekly structured training with a crew.<br/>"
            "Academy: cohort-based programs for learning or improving.",
            bg_color="#ecfeff",
            border_color="#0891b2",
        )
        + cta_button("Choose and Activate Membership", activation_url)
        + (
            f'<p style="font-size: 14px; color: #64748b;">After activation, '
            f'you can book this session here: <a href="{booking_url}">{booking_url}</a></p>'
        )
        + sign_off(
            "If you want to ask about a first-timer visit before paying, reply "
            "to this email and we will confirm what is possible for that session."
        )
    )

    html_body = wrap_html(
        title="Choose Your SwimBuddz Path",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Community swim on {session_date}",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_session_reminder_email(
    to_email: str,
    member_name: str,
    session_id: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str = "",
    reminder_type: str = "24h",
    pool_fee: float = 0,
    currency: str = "NGN",
    weather_summary: dict | None = None,
) -> bool:
    """
    Send session reminder email (24h, 3h, or 1h before).

    Args:
        to_email: Recipient email address.
        member_name: Recipient's first name.
        session_id: UUID of the booked session.
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
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    booking_url = f"{frontend_url}/sessions/{session_id}/book"
    weather_text = _weather_text(weather_summary)
    weather_html = _weather_html(weather_summary)
    body = f"""Hi {member_name},

{intro_message}

{session_title}
📅 {session_date}
⏰ {session_time}
📍 {session_location}
{f"🗺️ {session_address}" if session_address else ""}
{weather_text}

What to bring:
✓ Swimwear and swim cap
✓ Goggles
✓ Towel
✓ Water bottle

See you there! 🏊‍♂️

View your booking:
{booking_url}

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
        + weather_html
        + checklist_html
        + cta_button("View Booking", booking_url)
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
    articles: list[dict] | None = None,
    digest_configs: dict[str, dict] | None = None,
    preferences_url: str | None = None,
) -> bool:
    """Send a tier-sectioned weekly booking digest."""
    articles = articles or []
    digest_configs = digest_configs or {}
    if not sessions and not articles:
        return True

    subject = f"Your SwimBuddz week - {week_label}"
    audience_labels = {
        "community": "Community swims",
        "club": "Club training",
        "academy": "Academy classes",
    }
    audience_colors = {
        "community": "#0891b2",
        "club": "#2563eb",
        "academy": "#16a34a",
    }
    grouped = {
        audience: [s for s in sessions if s.get("audience") == audience]
        for audience in audience_labels
    }

    text_sections: list[str] = []
    for audience, label in audience_labels.items():
        audience_sessions = grouped[audience]
        if not audience_sessions:
            continue
        lines = [f"{label}:"]
        for session in audience_sessions:
            lines.append(
                f"- {session['title']} - {session['date']} at {session['time']} "
                f"({session['location']})\n  {session['state_label']}: "
                f"{session['action_url']}"
            )
            if session.get("weather_text"):
                lines.append(f"  Weather: {session['weather_text']}")
            if session.get("transport_text"):
                lines.append(f"  Transport: {session['transport_text']}")
        gear = (digest_configs.get(audience) or {}).get("default_gear_notes")
        if gear:
            lines.append(f"Gear: {gear}")
        text_sections.append("\n".join(lines))

    if articles:
        article_lines = ["Worth reading:"] + [
            f"- {article['title']} - {article['url']}" for article in articles
        ]
        text_sections.append("\n".join(article_lines))

    body = f"""Hi {member_name},

Here are the sessions you can attend this week, with your current booking state.

{chr(10).join(text_sections)}

Manage email preferences: {preferences_url or settings.FRONTEND_URL + '/account/settings'}

The SwimBuddz Team
"""

    html_sections = ""
    for audience, label in audience_labels.items():
        audience_sessions = grouped[audience]
        if not audience_sessions:
            continue
        config = digest_configs.get(audience) or {}
        color = audience_colors[audience]
        image_html = ""
        if config.get("featured_image_url"):
            image_html = (
                f'<img src="{escape(str(config["featured_image_url"]))}" '
                f'alt="{escape(str(config.get("image_alt") or label))}" '
                'style="display:block;width:100%;max-height:260px;object-fit:cover;'
                'border-radius:6px;margin:10px 0 16px;"/>'
            )
        intro_html = (
            f'<p style="margin:0 0 12px;color:#475569;font-size:14px;">'
            f'{escape(str(config.get("section_intro")))}</p>'
            if config.get("section_intro")
            else ""
        )
        cards = ""
        for session in audience_sessions:
            details = [
                f"{escape(str(session['date']))} at {escape(str(session['time']))}",
                escape(str(session["location"])),
            ]
            if session.get("scope_label"):
                details.append(escape(str(session["scope_label"])))
            if session.get("leader_label"):
                details.append(escape(str(session["leader_label"])))
            facts = " &nbsp;|&nbsp; ".join(details)
            purpose = (
                f'<p style="margin:8px 0;color:#475569;font-size:14px;">'
                f'{escape(str(session["purpose"]))}</p>'
                if session.get("purpose")
                else ""
            )
            operational_lines = []
            if session.get("weather_text"):
                operational_lines.append(
                    f"<strong>Weather:</strong> {escape(str(session['weather_text']))}"
                )
            if session.get("transport_text"):
                operational_lines.append(
                    f"<strong>Transport:</strong> {escape(str(session['transport_text']))}"
                )
            if session.get("fee_text"):
                operational_lines.append(
                    f"<strong>Fee:</strong> {escape(str(session['fee_text']))}"
                )
            if session.get("availability_text"):
                operational_lines.append(
                    f"<strong>Availability:</strong> "
                    f"{escape(str(session['availability_text']))}"
                )
            operations = ""
            if operational_lines:
                operations = (
                    '<p style="margin:10px 0 0;color:#334155;font-size:13px;'
                    'line-height:1.6;">' + "<br/>".join(operational_lines) + "</p>"
                )
            state_color = "#166534" if session.get("is_booked") else "#1d4ed8"
            cards += f"""
            <div style="border:1px solid #e2e8f0;border-left:4px solid {color};
                        border-radius:6px;padding:16px;margin:12px 0;background:#ffffff;">
              <div style="font-size:12px;font-weight:700;text-transform:uppercase;
                          color:{color};margin-bottom:5px;">{escape(label)}</div>
              <div style="font-size:17px;font-weight:700;color:#0f172a;">
                {escape(str(session['title']))}
              </div>
              {purpose}
              <p style="margin:8px 0;color:#64748b;font-size:13px;line-height:1.55;">
                {facts}
              </p>
              {operations}
              <p style="margin:12px 0 10px;color:{state_color};font-size:13px;
                        font-weight:700;">{escape(str(session['state_label']))}</p>
              <a href="{escape(str(session['action_url']))}"
                 style="display:inline-block;background:{color};color:#ffffff;
                        text-decoration:none;font-weight:700;font-size:13px;
                        padding:10px 16px;border-radius:5px;">
                {escape(str(session['action_label']))}
              </a>
              <a href="{escape(str(session['calendar_url']))}"
                 style="display:inline-block;margin-left:12px;color:#475569;
                        font-size:13px;font-weight:600;">Add to calendar</a>
            </div>
            """
        gear_html = ""
        if config.get("default_gear_notes"):
            gear_html = info_box(
                "<strong>What to bring</strong><br/>"
                + escape(str(config["default_gear_notes"])),
                bg_color="#f8fafc",
                border_color=color,
            )
        html_sections += (
            f'<div style="margin:26px 0 30px;">'
            f'<h2 style="font-size:21px;color:#0f172a;margin:0 0 6px;">'
            f"{escape(label)}</h2>{intro_html}{image_html}{cards}{gear_html}</div>"
        )

    article_cards = ""
    for article in articles:
        image_html = ""
        if article.get("image_url"):
            image_html = (
                f'<img src="{escape(str(article["image_url"]))}" '
                f'alt="{escape(str(article["title"]))}" '
                'style="display:block;width:100%;max-height:220px;object-fit:cover;'
                'border-radius:6px;margin-bottom:12px;"/>'
            )
        article_cards += f"""
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:16px;
                    margin:12px 0;background:#ffffff;">
            {image_html}
            <strong style="color:#1e293b;">{escape(str(article["title"]))}</strong><br/>
            <span style="font-size:14px;color:#64748b;">
                {escape(str(article.get("summary") or article.get("category") or "New article"))}
            </span><br/>
            <a href="{escape(str(article["url"]))}" style="font-size:14px;
               color:#0891b2;font-weight:700;">Read article</a>
        </div>
        """

    articles_section = (
        '<div style="margin:26px 0;"><h2 style="font-size:21px;color:#0f172a;">'
        "Worth reading</h2>" + article_cards + "</div>"
        if article_cards
        else ""
    )
    preferences_link = escape(
        preferences_url or f"{settings.FRONTEND_URL.rstrip('/')}/account/settings"
    )

    body_html = (
        f"<p>Hi {escape(member_name)},</p>"
        "<p>Here are the sessions you can attend this week, with your current "
        "booking state and the details you need to plan.</p>"
        + html_sections
        + articles_section
        + f'<p style="margin-top:28px;font-size:12px;color:#64748b;">'
        f'<a href="{preferences_link}" style="color:#475569;">Manage email preferences</a>'
        "</p>" + sign_off("See you in the water.")
    )

    html_body = wrap_html(
        title="Your SwimBuddz week",
        subtitle=week_label,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Your eligible sessions and booking status - {week_label}",
    )

    return await send_email(to_email, subject, body, html_body)
