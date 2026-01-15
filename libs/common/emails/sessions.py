"""
Session-related email templates.
"""

import os

from libs.common.emails.core import send_email


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
        # Build text version
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

        # Build HTML version
        ride_details_html = f"""
            <p style="margin: 5px 0;"><span style="color: #64748b;">📍 Area:</span> <strong>{ride_share_area}</strong></p>
            <p style="margin: 5px 0;"><span style="color: #64748b;">🚏 Pickup Location:</span> <strong>{pickup_location}</strong></p>
        """
        if pickup_description:
            ride_details_html += f"""
            <p style="margin: 5px 0; padding-left: 24px; font-size: 14px; color: #475569;">{pickup_description}</p>
            """
        ride_details_html += f"""
            <p style="margin: 5px 0;"><span style="color: #64748b;">🕐 Departure Time:</span> <strong>{departure_time or "TBD"}</strong></p>
        """
        if ride_distance:
            ride_details_html += f"""
            <p style="margin: 5px 0;"><span style="color: #64748b;">📏 Distance to Pool:</span> <strong>{ride_distance}</strong></p>
            """
        if ride_duration:
            ride_details_html += f"""
            <p style="margin: 5px 0;"><span style="color: #64748b;">⏱️ Estimated Duration:</span> <strong>{ride_duration}</strong></p>
            """

        ride_share_html = f"""
            <div style="background: #f0fdf4; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #22c55e;">
                <h3 style="margin: 0 0 15px 0; color: #166534;">🚗 Ride Share Details</h3>
                {ride_details_html}
                <p style="margin: 15px 0 0 0; font-size: 14px; color: #166534; background: #dcfce7; padding: 10px; border-radius: 6px;">
                    ⏰ Please arrive at the pickup location at least 5 minutes before departure.
                </p>
            </div>
        """

    # Member verification URL - use environment variable for different environments
    frontend_url = os.getenv("FRONTEND_URL", "https://swimbuddz.com")
    verify_url = f"{frontend_url}/verify/{member_id}"

    subject = f"Session Confirmed: {session_title} on {session_date}"

    body = f"""Hi {member_name},

Your session has been confirmed! 🎉

Session Details:
- Session: {session_title}
- Date: {session_date}
- Time: {session_time}
- Location: {session_location}
{f"- Address: {session_address}" if session_address else ""}
- Amount Paid: {amount_display}
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

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #06b6d4 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .session-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .session-box p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .ecard-box {{ background: linear-gradient(135deg, #0d9488 0%, #0891b2 100%); padding: 20px; border-radius: 12px; margin: 20px 0; text-align: center; color: white; }}
        .ecard-box a {{ display: inline-block; background: white; color: #0891b2; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-top: 10px; }}
        .checklist {{ background: #fefce8; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .checklist h4 {{ margin: 0 0 10px 0; color: #854d0e; }}
        .checklist ul {{ margin: 0; padding-left: 20px; color: #713f12; }}
        .checklist li {{ margin: 5px 0; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Session Confirmed!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">{session_title}</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Your session has been confirmed! Here are your details:</p>
            
            <div class="session-box">
                <p><span class="label">📅 Date:</span> <span class="value">{session_date}</span></p>
                <p><span class="label">⏰ Time:</span> <span class="value">{session_time}</span></p>
                <p><span class="label">📍 Location:</span> <span class="value">{session_location}</span></p>
                {f'<p><span class="label">🗺️ Address:</span> <span class="value">{session_address}</span></p>' if session_address else ""}
                <p><span class="label">💳 Amount Paid:</span> <span class="value">{amount_display}</span></p>
            </div>
            
            {ride_share_html}
            
            <div class="ecard-box">
                <h3 style="margin: 0;">Your E-Card</h3>
                <p style="margin: 10px 0; opacity: 0.9; font-size: 14px;">
                    Show this to pool staff for verification
                </p>
                <a href="{verify_url}">View E-Card →</a>
            </div>
            
            <div class="checklist">
                <h4>🎒 What to Bring</h4>
                <ul>
                    <li>Swimwear and swim cap</li>
                    <li>Goggles</li>
                    <li>Towel</li>
                    <li>Water bottle</li>
                    <li>This confirmation email or your e-card</li>
                </ul>
            </div>
            
            <p style="font-size: 14px; color: #64748b;">
                Need to cancel? Please contact us at least 24 hours before the session.
            </p>
            
            <p>See you in the water! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)
