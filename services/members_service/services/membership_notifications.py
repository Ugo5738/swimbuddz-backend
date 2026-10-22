"""Member-facing confirmations of committed pod and Club location changes."""

from html import escape

from libs.common.config import get_settings
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification
from services.members_service.models import Member

logger = get_logger(__name__)


async def notify_membership_change(db, *, member_id, title: str, body: str) -> None:
    """Email and notify after commit; delivery failure never undoes the change."""
    try:
        member = await db.get(Member, member_id)
        if not member:
            return
        action_url = "/account/club"
        link = f"{get_settings().FRONTEND_URL.rstrip('/')}{action_url}"
        message = (
            f"Hi {member.first_name},\n\n{body}\n\nView your Club: {link}\n\nSwimBuddz"
        )
        sent = await get_email_client().send(
            to_email=member.email,
            subject=title,
            body=message,
            html_body=f"<p>Hi {escape(member.first_name)},</p><p>{escape(body)}</p>"
            f'<p><a href="{escape(link, quote=True)}">View your Club</a></p><p>SwimBuddz</p>',
        )
        if not sent:
            logger.error(
                "Membership change email failed for member %s: %s", member_id, title
            )
    except Exception:
        logger.exception("Membership change email failed for member %s", member_id)
    try:
        await dispatch_notification(
            type="club_membership_changed",
            category="sessions",
            member_ids=[str(member_id)],
            title=title,
            body=body,
            action_url="/account/club",
            icon="users",
            calling_service="members",
        )
    except Exception:
        logger.exception(
            "Membership change in-app alert failed for member %s", member_id
        )
