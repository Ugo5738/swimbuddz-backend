"""
Payment-related email templates.
"""
from libs.common.emails.core import send_email


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
