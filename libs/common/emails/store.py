"""
Store-related email templates.
"""

from typing import Optional

from libs.common.emails.core import send_email


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
