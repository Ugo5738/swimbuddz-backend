"""
Session-related email templates.
"""

from libs.common.config import get_settings
from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    checklist_box,
    detail_box,
    info_box,
    sign_off,
    wrap_html,
)

settings = get_settings()


async def send_session_confirmation_email(
    to_email: str,
    member_name: str,
    member_id: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str = "",
    amount_paid: float = 0,
    ride_share_area: str | None = None,
    pickup_location: str | None = None,
    pickup_description: str | None = None,
    departure_time: str | None = None,
    ride_distance: str | None = None,
    ride_duration: str | None = None,
    currency: str = "NGN",
    bubbles_applied: int | None = None,
    bubbles_amount_ngn: float | None = None,
    bundle_info: str | None = None,
) -> bool:
    """
    Send session confirmation email to a member after successful payment.
    Includes session details, ride share info if booked, and e-card verification.
    """
    amount_display = (
        f"₦{amount_paid:,.0f}"
        if currency == "NGN"
        else f"{currency} {amount_paid:,.2f}"
    )

    # Build ride share section
    ride_share_text = ""
    ride_share_html = ""
    if ride_share_area and pickup_location:
        ride_share_text = f"""
Ride Share Details:
- Area: {ride_share_area}
- Pickup Location: {pickup_location}
{f"- Pickup Description: {pickup_description}" if pickup_description else ""}
- Departure Time: {departure_time or "TBD"}
{f"- Distance to Pool: {ride_distance}" if ride_distance else ""}
{f"- Estimated Duration: {ride_duration}" if ride_duration else ""}

Please be at the pickup location at least 5 minutes before departure.
"""

        ride_items = {
            "📍 Area": ride_share_area,
            "🚏 Pickup": pickup_location,
            "🕐 Departure": departure_time or "TBD",
        }
        if ride_distance:
            ride_items["📏 Distance"] = ride_distance
        if ride_duration:
            ride_items["⏱️ Duration"] = ride_duration

        ride_share_html = (
            '<h3 style="color: #166534;">🚗 Ride Share Details</h3>'
            + detail_box(ride_items, accent_color="#22c55e")
        )
        if pickup_description:
            ride_share_html += (
                f'<p style="font-size: 14px; color: #475569; margin-top: -12px;">'
                f"{pickup_description}</p>"
            )
        ride_share_html += info_box(
            "⏰ Please arrive at the pickup location at least 5 minutes before departure.",
            bg_color="#dcfce7",
            border_color="#22c55e",
        )

    # Member verification URL
    frontend_url = settings.FRONTEND_URL
    verify_url = f"{frontend_url}/verify/{member_id}"

    subject = f"Session Confirmed: {session_title} on {session_date}"

    bundle_line = f"\n{bundle_info}\n" if bundle_info else ""
    bubbles_line = (
        f"- Bubbles Applied: -₦{bubbles_amount_ngn:,.0f} ({bubbles_applied} 🫧)\n"
        if bubbles_applied and bubbles_amount_ngn
        else ""
    )

    body = f"""Hi {member_name},

Your session has been confirmed! 🎉
{bundle_line}
Session Details:
- Session: {session_title}
- Date: {session_date}
- Time: {session_time}
- Location: {session_location}
{f"- Address: {session_address}" if session_address else ""}
{bubbles_line}- Amount Paid: {amount_display}
{ride_share_text}
Your E-Card:
Show this to pool staff for verification: {verify_url}

What to Bring:
✓ Swimwear and swim cap
✓ Goggles
✓ Towel
✓ Water bottle
✓ This confirmation email or your e-card on the app

Need to cancel? Please contact us at least 24 hours before the session.

See you in the water! 🏊‍♂️

— The SwimBuddz Team
"""

    details = {
        "📅 Date": session_date,
        "⏰ Time": session_time,
        "📍 Location": session_location,
        "🗺️ Address": session_address,
    }
    if bubbles_applied and bubbles_amount_ngn:
        details[f"🫧 Bubbles Applied ({bubbles_applied})"] = (
            f"-₦{bubbles_amount_ngn:,.0f}"
        )
    details["💳 Amount Paid"] = amount_display

    ecard_html = (
        '<div style="background: linear-gradient(135deg, #0d9488 0%, #0891b2 100%); '
        'padding: 24px; border-radius: 12px; margin: 24px 0; text-align: center; color: white;">'
        '<h3 style="margin: 0; color: white;">Your E-Card</h3>'
        '<p style="margin: 8px 0 16px; opacity: 0.9; font-size: 14px; color: white;">'
        "Show this to pool staff for verification</p>"
        f'<a href="{verify_url}" style="display: inline-block; background: white; '
        "color: #0891b2; padding: 12px 24px; border-radius: 8px; text-decoration: none; "
        'font-weight: 600;">View E-Card &rarr;</a></div>'
    )

    bundle_html = (
        f'<div style="background: #ecfeff; border-left: 3px solid #0891b2; '
        f'padding: 10px 14px; border-radius: 6px; margin: 12px 0; font-size: 14px; color: #155e75;">'
        f"📦 {bundle_info}</div>"
        if bundle_info
        else ""
    )

    body_html = (
        f"<p>Hi {member_name},</p>"
        "<p>Your session has been confirmed! Here are your details:</p>"
        + bundle_html
        + detail_box(details)
        + ride_share_html
        + ecard_html
        + checklist_box(
            "🎒 What to Bring",
            [
                "Swimwear and swim cap",
                "Goggles",
                "Towel",
                "Water bottle",
                "This confirmation email or your e-card",
            ],
        )
        + '<p style="font-size: 14px; color: #64748b;">'
        "Need to cancel? Please contact us at least 24 hours before the session.</p>"
        + sign_off("See you in the water! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="🏊\u200d♂️ Session Confirmed!",
        subtitle=session_title,
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Your session on {session_date} is confirmed",
    )

    return await send_email(to_email, subject, body, html_body)


async def send_ride_share_confirmation_email(
    to_email: str,
    member_name: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    amount_paid: float = 0,
    ride_share_area: str | None = None,
    pickup_location: str | None = None,
    pickup_description: str | None = None,
    departure_time: str | None = None,
    num_seats: int = 1,
    currency: str = "NGN",
) -> bool:
    """
    Send ride share confirmation email after a standalone ride share purchase.
    Different from session_confirmation — focuses on ride details, not session booking.
    """
    amount_display = (
        f"₦{amount_paid:,.0f}"
        if currency == "NGN"
        else f"{currency} {amount_paid:,.2f}"
    )

    seats_text = f" ({num_seats} seats)" if num_seats > 1 else ""

    subject = f"Ride Share Confirmed: {session_title} on {session_date}"

    body = f"""Hi {member_name},

Your ride share has been confirmed! 🚗

Ride Details:
- Session: {session_title}
- Date: {session_date}
- Time: {session_time}
- Pool: {session_location}
- Area: {ride_share_area or "—"}
- Pickup: {pickup_location or "—"}
{f"- Pickup Info: {pickup_description}" if pickup_description else ""}
- Departure: {departure_time or "TBD"}
- Seats: {num_seats}
- Amount Paid: {amount_display}

Please be at the pickup location at least 5 minutes before departure.

See you poolside! 🏊‍♂️

— The SwimBuddz Team
"""

    ride_items = {
        "📍 Area": ride_share_area or "—",
        "🚏 Pickup": pickup_location or "—",
        "🕐 Departure": departure_time or "TBD",
        "💺 Seats": str(num_seats),
        "💳 Amount Paid": amount_display,
    }

    session_items = {
        "🏊 Session": session_title,
        "📅 Date": session_date,
        "⏰ Time": session_time,
        "📍 Pool": session_location,
    }

    body_html = (
        f"<p>Hi {member_name},</p>"
        f"<p>Your ride share{seats_text} has been confirmed! Here are your details:</p>"
        + '<h3 style="color: #166534;">🚗 Ride Details</h3>'
        + detail_box(ride_items, accent_color="#22c55e")
    )
    if pickup_description:
        body_html += (
            f'<p style="font-size: 14px; color: #475569; margin-top: -12px;">'
            f"{pickup_description}</p>"
        )
    body_html += (
        info_box(
            "⏰ Please arrive at the pickup location at least 5 minutes before departure.",
            bg_color="#dcfce7",
            border_color="#22c55e",
        )
        + '<h3 style="color: #0891b2;">📋 Session Info</h3>'
        + detail_box(session_items)
        + sign_off("See you poolside! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="🚗 Ride Share Confirmed!",
        subtitle=f"{ride_share_area or 'Ride'} → {session_location}",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"Your ride to {session_title} on {session_date} is confirmed",
    )

    return await send_email(to_email, subject, body, html_body)
