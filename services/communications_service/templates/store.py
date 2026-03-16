"""
Store-related email templates.
"""

from typing import Optional

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    GRADIENT_CYAN,
    GRADIENT_GREEN,
    info_box,
    sign_off,
    wrap_html,
)


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
    bubbles_applied: Optional[int] = None,
    bubbles_amount_ngn: Optional[float] = None,
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
{f"Bubbles Applied: -₦{bubbles_amount_ngn:,.0f} ({bubbles_applied} 🫧)" if bubbles_applied and bubbles_amount_ngn else ""}
Total: ₦{total:,.0f}

{fulfillment_text}

We'll notify you when your order is ready for {"pickup" if fulfillment_type == "pickup" else "delivery"}.

Thank you for shopping with SwimBuddz!

— The SwimBuddz Team
"""

    # Build order table
    items_html = "".join(
        f"<tr><td style='padding: 10px; border-bottom: 1px solid #e2e8f0;'>{item['name']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: center;'>{item['quantity']}</td>"
        f"<td style='padding: 10px; border-bottom: 1px solid #e2e8f0; text-align: right;'>₦{item['price']:,.0f}</td></tr>"
        for item in items
    )

    table_html = (
        '<div style="background: #f8fafc; border-radius: 8px; padding: 20px; margin: 20px 0;">'
        '<table style="width: 100%; border-collapse: collapse;">'
        "<thead><tr>"
        '<th style="padding: 10px; text-align: left; color: #64748b; font-size: 12px; text-transform: uppercase; border-bottom: 2px solid #e2e8f0;">Item</th>'
        '<th style="padding: 10px; text-align: center; color: #64748b; font-size: 12px; text-transform: uppercase; border-bottom: 2px solid #e2e8f0;">Qty</th>'
        '<th style="padding: 10px; text-align: right; color: #64748b; font-size: 12px; text-transform: uppercase; border-bottom: 2px solid #e2e8f0;">Price</th>'
        "</tr></thead>"
        f"<tbody>{items_html}</tbody></table>"
        '<div style="margin-top: 16px; padding-top: 16px; border-top: 2px solid #e2e8f0;">'
        f'<p style="margin: 5px 0; display: flex; justify-content: space-between; font-size: 14px;"><span>Subtotal</span><span>₦{subtotal:,.0f}</span></p>'
    )
    if discount > 0:
        table_html += f'<p style="margin: 5px 0; display: flex; justify-content: space-between; font-size: 14px;"><span>Discount</span><span>-₦{discount:,.0f}</span></p>'
    if delivery_fee > 0:
        table_html += f'<p style="margin: 5px 0; display: flex; justify-content: space-between; font-size: 14px;"><span>Delivery Fee</span><span>₦{delivery_fee:,.0f}</span></p>'
    if bubbles_applied and bubbles_amount_ngn:
        table_html += (
            f'<p style="margin: 5px 0; display: flex; justify-content: space-between; font-size: 14px;">'
            f"<span>Bubbles Applied ({bubbles_applied} \U0001FAE7)</span>"
            f"<span>-₦{bubbles_amount_ngn:,.0f}</span></p>"
        )
    table_html += (
        f'<p style="margin: 10px 0 0; display: flex; justify-content: space-between; font-weight: 700; font-size: 18px; color: #1e293b;">'
        f"<span>Total</span><span>₦{total:,.0f}</span></p>"
        "</div></div>"
    )

    fulfillment_icon = "📍" if fulfillment_type == "pickup" else "🚚"
    fulfillment_label = (
        "Pickup Location" if fulfillment_type == "pickup" else "Delivery Address"
    )
    fulfillment_value = (
        pickup_location if fulfillment_type == "pickup" else delivery_address
    )

    body_html = (
        f"<p>Hi {customer_name},</p>"
        "<p>Thank you for your order! We've received your payment and your order is now being processed.</p>"
        + table_html
        + info_box(
            f"<strong>{fulfillment_icon} {fulfillment_label}</strong><br/>{fulfillment_value}",
            bg_color="#ecfeff",
            border_color="#0891b2",
        )
        + f"<p>We'll notify you when your order is ready for {'pickup' if fulfillment_type == 'pickup' else 'delivery'}.</p>"
        + sign_off("Thank you for shopping with SwimBuddz! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title="🛒 Order Confirmed!",
        subtitle=f"Order #{order_number}",
        body_html=body_html,
        header_gradient=GRADIENT_CYAN,
        preheader=f"Order #{order_number} confirmed - ₦{total:,.0f}",
    )

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
        action_html = info_box(
            f"<strong>📍 Pickup Location</strong><br/>{pickup_location}<br/><br/>"
            "<em>Please bring your order confirmation email or ID when collecting.</em>",
            bg_color="#ecfeff",
            border_color="#0891b2",
        )
        emoji = "📦"
        title = "Ready for Pickup!"
    else:
        subject = f"Your Order #{order_number} has been Shipped!"
        tracking_info = (
            f"\n\nTracking Number: {tracking_number}" if tracking_number else ""
        )
        action_text = f"Your order is on its way!{tracking_info}"
        tracking_html = (
            f"<br/><br/><strong>Tracking Number:</strong> {tracking_number}"
            if tracking_number
            else ""
        )
        action_html = info_box(
            f"<strong>🚚 Order Shipped!</strong><br/>Your order is on its way to you.{tracking_html}",
            bg_color="#f0fdf4",
            border_color="#22c55e",
        )
        emoji = "🚚"
        title = "Order Shipped!"

    body = f"""Hi {customer_name},

Great news! {title}

Order #{order_number}

{action_text}

Thank you for shopping with SwimBuddz!

— The SwimBuddz Team
"""

    body_html = (
        f"<p>Hi {customer_name},</p>"
        "<p>Great news!</p>"
        + action_html
        + sign_off("Thank you for shopping with SwimBuddz! 🏊\u200d♂️")
    )

    html_body = wrap_html(
        title=f"{emoji} {title}",
        subtitle=f"Order #{order_number}",
        body_html=body_html,
        header_gradient=GRADIENT_GREEN,
        preheader=f"Order #{order_number} - {title}",
    )

    return await send_email(to_email, subject, body, html_body)
