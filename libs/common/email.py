"""
Email sending utilities using Brevo SMTP.
"""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from libs.common.logging import get_logger

logger = get_logger(__name__)

# Brevo SMTP settings
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_USERNAME = "9e85f2001@smtp-brevo.com"
DEFAULT_FROM_EMAIL = "no-reply@swimbuddz.com"
DEFAULT_FROM_NAME = "SwimBuddz"


async def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
) -> bool:
    """
    Send an email using Brevo SMTP.

    Args:
        to_email: Recipient email address
        subject: Email subject line
        body: Plain text body
        html_body: Optional HTML body (if not provided, plain text is used)
        from_email: Sender email (defaults to no-reply@swimbuddz.com)
        from_name: Sender name (defaults to SwimBuddz)

    Returns:
        True if email was sent successfully, False otherwise
    """
    smtp_password = os.environ.get("BREVO_KEY")

    if not smtp_password:
        logger.warning("BREVO_KEY not found in environment - email not sent")
        logger.info(f"Would have sent email to {to_email}: {subject}")
        logger.debug(f"Email body: {body[:200]}...")
        return False

    sender_email = from_email or DEFAULT_FROM_EMAIL
    sender_name = from_name or DEFAULT_FROM_NAME

    try:
        # Create message
        if html_body:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))
        else:
            msg = MIMEText(body, "plain")

        msg["Subject"] = subject
        msg["From"] = f"{sender_name} <{sender_email}>"
        msg["To"] = to_email

        logger.info(f"Sending email to {to_email}: {subject}")

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USERNAME, smtp_password)
            server.sendmail(sender_email, to_email, msg.as_string())

        logger.info(f"Email sent successfully to {to_email}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed: {e}")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to send email: {type(e).__name__}: {e}")
        return False


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


async def send_enrollment_confirmation_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
) -> bool:
    """
    Send enrollment confirmation email to a member.
    """
    subject = f"Welcome to {program_name}! Your enrollment is confirmed."

    body = f"""Hi {member_name},

Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date}

What's Next:
- Sessions will appear in your Sessions page once they're scheduled
- Make sure your profile is complete with emergency contact information
- Review the program curriculum on your Academy dashboard

If you have any questions, please reach out to our team.

See you in the water!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Welcome to {program_name}!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your enrollment has been confirmed</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>Congratulations! Your enrollment in the SwimBuddz Academy has been confirmed.</p>
            
            <div class="details">
                <p><span class="label">Program:</span> <span class="value">{program_name}</span></p>
                <p><span class="label">Cohort:</span> <span class="value">{cohort_name}</span></p>
                <p><span class="label">Start Date:</span> <span class="value">{start_date}</span></p>
            </div>
            
            <h3>What's Next:</h3>
            <ul>
                <li>Sessions will appear in your Sessions page once they're scheduled</li>
                <li>Make sure your profile is complete with emergency contact information</li>
                <li>Review the program curriculum on your Academy dashboard</li>
            </ul>
            
            <p>If you have any questions, please reach out to our team.</p>
            
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


async def send_payment_approved_email(
    to_email: str,
    payment_reference: str,
    purpose: str,
    amount: float,
    currency: str = "NGN",
) -> bool:
    """
    Send payment approval notification to a member.
    """
    purpose_display = purpose.replace("_", " ").title()
    amount_display = (
        f"₦{amount:,.0f}" if currency == "NGN" else f"{currency} {amount:,.2f}"
    )

    subject = f"Payment Approved - {purpose_display}"

    body = f"""Hi there,

Great news! Your manual payment has been verified and approved.

Payment Details:
- Reference: {payment_reference}
- Purpose: {purpose_display}
- Amount: {amount_display}

Your membership/enrollment has been activated. You can now access all associated features.

Thank you for being part of SwimBuddz!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #10b981; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">✅ Payment Approved!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your manual payment has been verified</p>
        </div>
        <div class="content">
            <p>Hi there,</p>
            <p>Great news! Your manual payment has been verified and approved.</p>
            
            <div class="details">
                <p><span class="label">Reference:</span> <span class="value">{payment_reference}</span></p>
                <p><span class="label">Purpose:</span> <span class="value">{purpose_display}</span></p>
                <p><span class="label">Amount:</span> <span class="value">{amount_display}</span></p>
            </div>
            
            <p>Your membership/enrollment has been activated. You can now access all associated features.</p>
            
            <p>Thank you for being part of SwimBuddz! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)


async def send_store_order_confirmation_email(
    to_email: str,
    customer_name: str,
    order_number: str,
    items: list[dict],  # [{"name": str, "quantity": int, "price": float}]
    subtotal: float,
    discount: float,
    delivery_fee: float,
    total: float,
    fulfillment_type: str,  # "pickup" or "delivery"
    pickup_location: Optional[str] = None,
    delivery_address: Optional[str] = None,
) -> bool:
    """
    Send order confirmation email when payment is successful.
    """
    subject = f"Order Confirmed - #{order_number}"

    # Build items list
    items_text = "\n".join(
        f"  - {item['name']} x{item['quantity']} - ₦{item['price']:,.0f}"
        for item in items
    )
    items_html = "".join(
        f"<tr><td>{item['name']}</td><td style='text-align:center'>{item['quantity']}</td><td style='text-align:right'>₦{item['price']:,.0f}</td></tr>"
        for item in items
    )

    fulfillment_text = (
        f"Pickup Location: {pickup_location}"
        if fulfillment_type == "pickup"
        else f"Delivery Address: {delivery_address}"
    )

    body = f"""Hi {customer_name},

Thank you for your order! We've received your payment and your order is now being processed.

Order #{order_number}

Items:
{items_text}

Subtotal: ₦{subtotal:,.0f}
{f"Discount: -₦{discount:,.0f}" if discount > 0 else ""}
{f"Delivery Fee: ₦{delivery_fee:,.0f}" if delivery_fee > 0 else ""}
Total: ₦{total:,.0f}

{fulfillment_text}

We'll notify you when your order is ready for {"pickup" if fulfillment_type == "pickup" else "delivery"}.

Thank you for shopping with SwimBuddz!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .order-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ color: #64748b; font-size: 12px; text-transform: uppercase; }}
        .totals {{ margin-top: 15px; padding-top: 15px; border-top: 2px solid #e2e8f0; }}
        .totals p {{ margin: 5px 0; display: flex; justify-content: space-between; }}
        .total-row {{ font-weight: bold; font-size: 18px; color: #1e293b; }}
        .fulfillment {{ background: #ecfeff; padding: 15px; border-radius: 8px; margin-top: 20px; border-left: 4px solid #0891b2; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🛒 Order Confirmed!</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Order #{order_number}</p>
        </div>
        <div class="content">
            <p>Hi {customer_name},</p>
            <p>Thank you for your order! We've received your payment and your order is now being processed.</p>
            
            <div class="order-box">
                <table>
                    <thead>
                        <tr>
                            <th>Item</th>
                            <th style="text-align:center">Qty</th>
                            <th style="text-align:right">Price</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>
                
                <div class="totals">
                    <p><span>Subtotal</span><span>₦{subtotal:,.0f}</span></p>
                    {f"<p><span>Discount</span><span>-₦{discount:,.0f}</span></p>" if discount > 0 else ""}
                    {f"<p><span>Delivery Fee</span><span>₦{delivery_fee:,.0f}</span></p>" if delivery_fee > 0 else ""}
                    <p class="total-row"><span>Total</span><span>₦{total:,.0f}</span></p>
                </div>
            </div>
            
            <div class="fulfillment">
                <strong>{"📍 Pickup Location" if fulfillment_type == "pickup" else "🚚 Delivery Address"}</strong><br/>
                {pickup_location if fulfillment_type == "pickup" else delivery_address}
            </div>
            
            <p>We'll notify you when your order is ready for {"pickup" if fulfillment_type == "pickup" else "delivery"}.</p>
            
            <p>Thank you for shopping with SwimBuddz! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)


async def send_store_order_ready_email(
    to_email: str,
    customer_name: str,
    order_number: str,
    fulfillment_type: str,
    pickup_location: Optional[str] = None,
    tracking_number: Optional[str] = None,
) -> bool:
    """
    Send notification when order is ready for pickup or shipped.
    """
    if fulfillment_type == "pickup":
        subject = f"Your Order #{order_number} is Ready for Pickup!"
        action_text = f"Your order is ready and waiting for you at:\n\n{pickup_location}\n\nPlease bring your order confirmation email or ID when collecting."
        action_html = f"""
            <div style="background: #ecfeff; padding: 20px; border-radius: 8px; border-left: 4px solid #0891b2;">
                <strong>📍 Pickup Location</strong><br/>
                {pickup_location}<br/><br/>
                <em>Please bring your order confirmation email or ID when collecting.</em>
            </div>
        """
        emoji = "📦"
        title = "Ready for Pickup!"
    else:
        subject = f"Your Order #{order_number} has been Shipped!"
        tracking_info = (
            f"\n\nTracking Number: {tracking_number}" if tracking_number else ""
        )
        action_text = f"Your order is on its way!{tracking_info}"
        action_html = f"""
            <div style="background: #f0fdf4; padding: 20px; border-radius: 8px; border-left: 4px solid #22c55e;">
                <strong>🚚 Order Shipped!</strong><br/>
                Your order is on its way to you.
                {f"<br/><br/><strong>Tracking Number:</strong> {tracking_number}" if tracking_number else ""}
            </div>
        """
        emoji = "🚚"
        title = "Order Shipped!"

    body = f"""Hi {customer_name},

Great news! {title}

Order #{order_number}

{action_text}

Thank you for shopping with SwimBuddz!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">{emoji} {title}</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Order #{order_number}</p>
        </div>
        <div class="content">
            <p>Hi {customer_name},</p>
            <p>Great news!</p>
            
            {action_html}
            
            <p style="margin-top: 20px;">Thank you for shopping with SwimBuddz! 🏊‍♂️</p>
            
            <div class="footer">
                <p>— The SwimBuddz Team</p>
            </div>
        </div>
    </div>
</body>
</html>
"""

    return await send_email(to_email, subject, body, html_body)


async def send_enrollment_reminder_email(
    to_email: str,
    member_name: str,
    program_name: str,
    cohort_name: str,
    start_date: str,
    start_time: str,
    location: str,
    days_until: int,
    prep_materials: Optional[list] = None,
) -> bool:
    """
    Send reminder email X days before cohort starts.
    Content varies based on days remaining (7, 3, 1).
    """
    days_text = f"{days_until} days" if days_until > 1 else "tomorrow"
    subject = f"Reminder: Your swimming course starts in {days_text}! 🏊‍♂️"

    if days_until == 1:
        subject = f"URGENT: Your swimming course starts tomorrow! 🏊‍♂️"

    urgency_msg = f"We're excited to see you in {days_text}!"
    if days_until == 1:
        urgency_msg = (
            "We're excited to see you tomorrow! Please double check your gear."
        )

    checklist_html = """
        <div class="checklist">
            <h4>🎒 Checklist</h4>
            <ul>
                <li>Swimwear and cap</li>
                <li>Goggles</li>
                <li>Towel and flip flops</li>
                <li>Water bottle</li>
            </ul>
        </div>
    """

    # Custom message based on timing
    tip_html = ""
    if days_until >= 7:
        tip_html = """
            <div class="session-box" style="border-left-color: #f59e0b;">
                <strong>💡 Pro Tip:</strong><br/>
                Now is a great time to try on your swimwear and make sure everything fits comfortably!
            </div>
        """
    elif days_until <= 3:
        tip_html = """
            <div class="session-box" style="border-left-color: #f59e0b;">
                <strong>💡 Preparation:</strong><br/>
                Hydrate well before your session and arrive 15 minutes early to change.
            </div>
        """

    body = f"""Hi {member_name},

{urgency_msg}

Program: {program_name}
Cohort: {cohort_name}
Start Date: {start_date} at {start_time}
Location: {location}

Checklist:
- Swimwear and cap
- Goggles
- Towel and flip flops
- Water bottle

See you soon!

— The SwimBuddz Team
"""

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #0891b2 0%, #0284c7 100%); color: white; padding: 30px; border-radius: 12px 12px 0 0; }}
        .content {{ background: #f8fafc; padding: 30px; border-radius: 0 0 12px 12px; }}
        .details {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .details p {{ margin: 8px 0; }}
        .label {{ color: #64748b; font-size: 14px; }}
        .value {{ font-weight: 600; color: #1e293b; }}
        .checklist {{ background: #fefce8; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .checklist h4 {{ margin: 0 0 10px 0; color: #854d0e; }}
        .checklist ul {{ margin: 0; padding-left: 20px; color: #713f12; }}
        .checklist li {{ margin: 5px 0; }}
        .session-box {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #0891b2; }}
        .footer {{ text-align: center; color: #64748b; font-size: 14px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0;">🏊‍♂️ Getting Ready?</h1>
            <p style="margin: 10px 0 0 0; opacity: 0.9;">Your course starts in {days_text}</p>
        </div>
        <div class="content">
            <p>Hi {member_name},</p>
            <p>{urgency_msg}</p>
            
            <div class="details">
                <p><span class="label">Program:</span> <span class="value">{program_name}</span></p>
                <p><span class="label">Cohort:</span> <span class="value">{cohort_name}</span></p>
                <p><span class="label">When:</span> <span class="value">{start_date} at {start_time}</span></p>
                <p><span class="label">Where:</span> <span class="value">{location}</span></p>
            </div>

            {tip_html}
            
            {checklist_html}
            
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
