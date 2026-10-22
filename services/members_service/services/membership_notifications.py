"""Queue operational Club emails in the transaction that changes membership."""

from html import escape
import uuid

from libs.common.config import get_settings
from libs.common.operational_email import enqueue_operational_email
from services.members_service.models import Member


async def notify_membership_change(
    db,
    *,
    member_id,
    title: str,
    body: str,
    event_key: str | None = None,
    action_url: str = "/account/club",
) -> None:
    member = await db.get(Member, member_id)
    if not member:
        return
    link = f"{get_settings().FRONTEND_URL.rstrip('/')}{action_url}"
    message = f"Hi {member.first_name},\n\n{body}\n\nView details: {link}\n\nSwimBuddz"
    await enqueue_operational_email(
        db,
        event_key=event_key or f"membership:{uuid.uuid4()}",
        recipient_email=member.email,
        member_id=member_id,
        subject=title,
        body=message,
        notification_body=body,
        html_body=f"<p>Hi {escape(member.first_name)},</p><p>{escape(body).replace(chr(10), '<br>')}</p>"
        f'<p><a href="{escape(link, quote=True)}">View details</a></p><p>SwimBuddz</p>',
        action_url=action_url,
    )
