"""
Payment-related email templates.
"""

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_GREEN,
    detail_box,
    sign_off,
    wrap_html,
)


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

    body_html = (
        "<p>Hi there,</p>"
        "<p>Great news! Your manual payment has been verified and approved.</p>"
        + detail_box(
            {
                "Reference": payment_reference,
                "Purpose": purpose_display,
                "Amount": amount_display,
            },
            accent_color="#10b981",
        )
        + "<p>Your membership/enrollment has been activated. You can now access all associated features.</p>"
        + sign_off("Thank you for being part of SwimBuddz! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="✅ Payment Approved!",
        subtitle="Your manual payment has been verified",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"Payment of {amount_display} for {purpose_display} approved",
    )

    return await send_email(to_email, subject, body, html_body)
