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


async def send_admin_payment_received_email(
    to_email: str,
    payment_reference: str,
    member_email: str,
    purpose: str,
    amount: float,
    bubbles_used: int,
    bubbles_value_ngn: float,
    paid_at: str,
    currency: str = "NGN",
    cash_amount: float = 0,
    payment_method: str = "Bubbles",
    fulfillment_status: str = "Applied",
    bubbles_status: str = "Collected",
) -> bool:
    """Notify operations of cash, wallet, or mixed receipts."""
    purpose_display = purpose.replace("_", " ").title()
    amount_display = (
        f"₦{amount:,.0f}" if currency == "NGN" else f"{currency} {amount:,.2f}"
    )
    bubbles_value_display = f"₦{bubbles_value_ngn:,.0f}"
    cash_display = (
        f"₦{cash_amount:,.0f}"
        if currency == "NGN"
        else f"{currency} {cash_amount:,.2f}"
    )
    subject = f"Payment Received - {purpose_display} - {payment_reference}"
    body = f"""A payment was received.

Reference: {payment_reference}
Member: {member_email}
Purpose: {purpose_display}
Purchase value: {amount_display}
Payment method: {payment_method}
Cash received: {cash_display}
Bubbles used: {bubbles_used}
Bubbles value: {bubbles_value_display}
Bubbles settlement: {bubbles_status}
Fulfillment: {fulfillment_status}
Paid at: {paid_at or "Not available"}

— SwimBuddz System
"""
    body_html = (
        "<p>A payment was received.</p>"
        + detail_box(
            {
                "Reference": escape(payment_reference),
                "Member": escape(member_email),
                "Purpose": escape(purpose_display),
                "Purchase value": escape(amount_display),
                "Payment method": escape(payment_method),
                "Cash received": escape(cash_display),
                "Bubbles used": f"{bubbles_used:,}",
                "Bubbles value": escape(bubbles_value_display),
                "Bubbles settlement": escape(bubbles_status),
                "Paid at": escape(paid_at or "Not available"),
                "Fulfillment": escape(fulfillment_status),
            },
            accent_color="#f59e0b",
        )
        + sign_off("SwimBuddz payment notification")
    )
    html_body = wrap_html(
        title="Payment Received",
        subtitle=escape(purpose_display),
        body_html=body_html,
        header_gradient=GRADIENT_AMBER,
        preheader=f"{amount_display} received — {payment_method}",
    )
    return await send_email(to_email, subject, body, html_body)


# Keep the previous template available during rolling deployments.
send_admin_bubbles_payment_received_email = send_admin_payment_received_email
