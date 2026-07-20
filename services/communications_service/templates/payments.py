"""
Payment-related email templates.
"""

from html import escape

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_GREEN,
    GRADIENT_AMBER,
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


async def send_admin_bubbles_payment_received_email(
    to_email: str,
    payment_reference: str,
    member_email: str,
    purpose: str,
    amount: float,
    bubbles_used: int,
    bubbles_value_ngn: float,
    paid_at: str,
    currency: str = "NGN",
) -> bool:
    """Notify an admin when a payment is settled entirely with Bubbles."""
    purpose_display = purpose.replace("_", " ").title()
    amount_display = (
        f"₦{amount:,.0f}" if currency == "NGN" else f"{currency} {amount:,.2f}"
    )
    bubbles_value_display = f"₦{bubbles_value_ngn:,.0f}"
    subject = f"Bubbles Payment Received - {purpose_display} - {payment_reference}"
    body = f"""A payment was settled entirely with Bubbles.

Reference: {payment_reference}
Member: {member_email}
Purpose: {purpose_display}
Purchase value: {amount_display}
Bubbles used: {bubbles_used}
Bubbles value: {bubbles_value_display}
Paid at: {paid_at or "Not available"}

— SwimBuddz System
"""
    body_html = (
        "<p>A payment was settled entirely with Bubbles.</p>"
        + detail_box(
            {
                "Reference": escape(payment_reference),
                "Member": escape(member_email),
                "Purpose": escape(purpose_display),
                "Purchase value": escape(amount_display),
                "Bubbles used": f"{bubbles_used:,}",
                "Bubbles value": escape(bubbles_value_display),
                "Paid at": escape(paid_at or "Not available"),
            },
            accent_color="#f59e0b",
        )
        + sign_off("SwimBuddz payment notification")
    )
    html_body = wrap_html(
        title="Bubbles Payment Received",
        subtitle=escape(purpose_display),
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"{amount_display} paid with {bubbles_used:,} Bubbles",
    )
    return await send_email(to_email, subject, body, html_body)
