"""Idempotent membership renewal reminders (email + in-app)."""

from __future__ import annotations

from datetime import datetime
from html import escape

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberMembership

logger = get_logger(__name__)


def reminder_offsets(
    tier: str, club_cycle_months: int | None = None
) -> tuple[int, ...]:
    """Days before expiry; negative values are post-expiry follow-ups."""
    if tier == "academy":
        return (14, 7, 1, 0)
    if tier == "club" and (club_cycle_months or 3) <= 3:
        return (14, 7, 3, 1, 0, -3)
    return (30, 14, 7, 1, 0, -7)


def reminder_delivery_key(
    tier: str,
    expiry: datetime,
    days: int,
    channel: str,
) -> str:
    """Stable per-expiry/channel key; a renewed date starts a new sequence."""
    return f"{tier}:{expiry.isoformat()}:{days}:{channel}"


def _message(
    tier: str, first_name: str, days: int, expiry_label: str
) -> tuple[str, str]:
    name = first_name or "Swimmer"
    tier_name = tier.title()
    if tier == "academy":
        subject = (
            "Your Academy access is ending soon"
            if days > 0
            else "Your Academy access has ended"
        )
        body = (
            f"Hi {name}, your Academy access "
            f"{'ends' if days > 0 else 'ended'} on {expiry_label}. "
            "Continue swimming with Club after graduation or browse the next Academy programme."
        )
        return subject, body
    if days > 0:
        subject = f"Your {tier_name} membership expires in {days} day{'s' if days != 1 else ''}"
        body = (
            f"Hi {name}, your {tier_name} access is active until {expiry_label}. "
            "Renew early and your new period will start after the time you already paid for."
        )
    elif days == 0:
        subject = f"Your {tier_name} membership expires today"
        body = f"Hi {name}, your {tier_name} access expires today ({expiry_label}). Renew to continue without interruption."
    else:
        subject = f"Renew your {tier_name} membership"
        body = f"Hi {name}, your {tier_name} access ended on {expiry_label}. Renew whenever you're ready to restore access."
    return subject, body


def _candidate_expiries(
    membership: MemberMembership,
) -> list[tuple[str, datetime | None, int | None]]:
    club_values = [
        value
        for value in (
            membership.club_paid_until,
            membership.post_academy_club_until,
        )
        if value is not None
    ]
    club_expiry = max(club_values) if club_values else None
    club_cycle = membership.club_billing_cycle_months
    if (
        membership.post_academy_club_until
        and club_expiry == membership.post_academy_club_until
        and (
            membership.club_paid_until is None
            or membership.post_academy_club_until > membership.club_paid_until
        )
    ):
        club_cycle = 1
    return [
        ("academy", membership.academy_paid_until, None),
        ("club", club_expiry, club_cycle),
        ("community", membership.community_paid_until, None),
    ]


async def send_membership_renewal_reminders() -> int:
    """Send due reminders once per entitlement date and delivery channel."""
    sent = 0
    async for db in get_async_db():
        try:
            now = utc_now()
            members = (
                (
                    await db.execute(
                        select(Member)
                        .join(MemberMembership, MemberMembership.member_id == Member.id)
                        .options(selectinload(Member.membership))
                        .where(Member.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )

            for member in members:
                membership = member.membership
                if not membership:
                    continue

                # Remind about the highest currently relevant tier only. This
                # avoids asking an Academy member to renew Community while
                # inherited access is still active.
                for tier, expiry, club_cycle in _candidate_expiries(membership):
                    if expiry is None:
                        continue
                    days = (expiry.date() - now.date()).days
                    if days not in reminder_offsets(tier, club_cycle):
                        continue

                    if (
                        tier == "club"
                        and membership.academy_paid_until
                        and membership.academy_paid_until > now
                    ):
                        continue
                    if tier == "community" and (
                        (
                            membership.academy_paid_until
                            and membership.academy_paid_until > now
                        )
                        or (
                            membership.club_paid_until
                            and membership.club_paid_until > now
                        )
                        or (
                            membership.post_academy_club_until
                            and membership.post_academy_club_until > now
                        )
                    ):
                        continue

                    expiry_key = expiry.isoformat()
                    subject, body = _message(
                        tier,
                        member.first_name,
                        days,
                        expiry.strftime("%B %d, %Y"),
                    )
                    reminder_state = dict(membership.renewal_reminders_sent or {})
                    action_url = (
                        "/upgrade/club/plan"
                        if tier == "academy"
                        else f"/account/billing?required={tier}"
                    )

                    in_app_key = reminder_delivery_key(tier, expiry, days, "in_app")
                    if not reminder_state.get(in_app_key):
                        result = await dispatch_notification(
                            type="membership_renewal_reminder",
                            category="academy" if tier == "academy" else "payments",
                            member_ids=[str(member.id)],
                            title=subject,
                            body=body,
                            action_url=action_url,
                            icon="credit-card",
                            metadata={
                                "tier": tier,
                                "paid_until": expiry_key,
                                "days_until_expiry": days,
                            },
                            calling_service="members",
                        )
                        if result is not None:
                            reminder_state[in_app_key] = True
                            sent += 1

                    email_key = reminder_delivery_key(tier, expiry, days, "email")
                    if member.email and not reminder_state.get(email_key):
                        delivered = await get_email_client().send(
                            to_email=member.email,
                            subject=subject,
                            body=body,
                            html_body=(
                                f"<p>{escape(body)}</p>"
                                f'<p><a href="https://www.swimbuddz.com{action_url}">'
                                "Manage membership</a></p>"
                            ),
                        )
                        if delivered:
                            reminder_state[email_key] = True
                            sent += 1

                    if reminder_state != (membership.renewal_reminders_sent or {}):
                        membership.renewal_reminders_sent = reminder_state
                        flag_modified(membership, "renewal_reminders_sent")
                    # Only the highest due tier should message this run.
                    break

            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Failed to send membership renewal reminders")
        finally:
            await db.close()
            break
    return sent
