"""Branded guest confirmations and assessment follow-up."""

from html import escape

from libs.common.emails.core import send_email
from services.communications_service.templates.base import (
    checklist_box,
    cta_button,
    detail_box,
    sign_off,
    wrap_html,
)


async def send_guest_pass_confirmation_email(
    to_email: str,
    guest_name: str,
    session_title: str,
    session_date: str,
    session_time: str,
    session_location: str,
    session_address: str,
    amount_paid: float,
    payment_reference: str,
    receipt_url: str,
    post_session: bool = False,
) -> bool:
    heading = (
        "Your guest swim booking has been recorded"
        if post_session
        else "Your guest swim is confirmed"
    )
    guidance = (
        "Thank you for completing your guest booking. Our team confirms attendance separately."
        if post_session
        else "Arrive 15 minutes early and show this email or your booking reference at check-in. Follow the pool team's safety instructions and tell your coach your swimming experience."
    )
    bring = [
        "Swimwear and swim cap",
        "Goggles",
        "Towel",
        "Water bottle",
        "Your booking reference",
    ]
    details = {
        "Session": session_title,
        "Date": session_date,
        "Time": session_time,
        "Location": session_location,
        "Address": session_address,
        "Amount paid": f"₦{amount_paid:,.2f}",
        "Reference": payment_reference,
    }
    body = f"Hi {guest_name},\n\n{heading}.\n\n" + "\n".join(
        f"{k}: {v}" for k, v in details.items() if v
    )
    body += f"\n\n{guidance}\n\nView your booking and receipt: {receipt_url}\n"
    if not post_session:
        body += "\nWhat to bring: " + ", ".join(bring) + ".\n"
    body += "\nKeep your private booking link safe. Contact SwimBuddz if you need help.\n\nThe SwimBuddz Team"
    html = (
        f"<p>Hi {escape(guest_name)},</p><p>{escape(guidance)}</p>"
        + detail_box({k: escape(v) for k, v in details.items() if v})
        + cta_button("View booking and receipt", escape(receipt_url, quote=True))
        + (checklist_box("What to bring", bring) if not post_session else "")
        + "<p>Keep your private booking link safe. Contact SwimBuddz if you need help.</p>"
        + sign_off(
            "Thank you for swimming with us!"
            if post_session
            else "See you at the pool!"
        )
    )
    return await send_email(
        to_email,
        f"{heading}: {session_title}",
        body,
        wrap_html(title=heading, subtitle=escape(session_title), body_html=html),
    )


async def send_guest_assessment_email(
    to_email: str, guest_name: str, assessment: dict
) -> bool:
    details = {
        key.replace("_", " ").title(): str(value) for key, value in assessment.items()
    }
    body = (
        f"Hi {guest_name},\n\nHere is the assessment from your swim:\n\n"
        + "\n".join(f"{key}: {value}" for key, value in details.items())
    )
    body += "\n\nTalk to your coach about the next step for your swimming.\n\nThe SwimBuddz Team"
    html = (
        f"<p>Hi {escape(guest_name)},</p>"
        + detail_box({escape(k): escape(v) for k, v in details.items()})
        + "<p>Talk to your coach about the next step for your swimming.</p>"
        + sign_off()
    )
    return await send_email(
        to_email,
        "Your SwimBuddz swim assessment",
        body,
        wrap_html(title="Your swim assessment", body_html=html),
    )
