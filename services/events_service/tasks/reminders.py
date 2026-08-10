"""Send idempotent event reminders to RSVPs and explicit invitees."""

from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification
from libs.db.session import get_async_db
from services.events_service.models import (
    Event,
    EventInvite,
    EventReminderLog,
    EventRSVP,
)

logger = get_logger(__name__)
DELIVERY_WINDOW = timedelta(minutes=10)
LOOKAHEAD = timedelta(days=31)


def due_reminder_offsets(event: Event, now: datetime) -> list[int]:
    """Return configured offsets whose delivery window currently contains now."""
    due = []
    for raw_hours in event.email_reminder_hours or []:
        hours = int(raw_hours)
        scheduled_for = event.start_time - timedelta(hours=hours)
        if scheduled_for <= now < scheduled_for + DELIVERY_WINDOW:
            due.append(hours)
    return due


def _offset_label(hours: int) -> str:
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} day" if days == 1 else f"{days} days"
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def _event_time(event: Event) -> str:
    try:
        zone = ZoneInfo(event.timezone)
    except Exception:
        zone = ZoneInfo("Africa/Lagos")
    return event.start_time.astimezone(zone).strftime("%A, %d %B %Y at %I:%M %p")


async def send_due_event_reminders() -> None:
    async for db in get_async_db():
        try:
            now = utc_now()
            events = (
                (
                    await db.execute(
                        select(Event).where(
                            Event.status == "published",
                            Event.start_time > now,
                            Event.start_time <= now + LOOKAHEAD,
                            Event.email_reminder_hours.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            sent = 0
            for event in events:
                for hours in due_reminder_offsets(event, now):
                    recipients = set(
                        (
                            await db.execute(
                                select(EventRSVP.member_id).where(
                                    EventRSVP.event_id == event.id,
                                    EventRSVP.status == "going",
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if event.visibility == "invite_only":
                        recipients.update(
                            (
                                await db.execute(
                                    select(EventInvite.member_id).where(
                                        EventInvite.event_id == event.id
                                    )
                                )
                            )
                            .scalars()
                            .all()
                        )
                    if not recipients:
                        continue

                    already_sent = set(
                        (
                            await db.execute(
                                select(EventReminderLog.member_id).where(
                                    EventReminderLog.event_id == event.id,
                                    EventReminderLog.reminder_hours == hours,
                                    EventReminderLog.member_id.in_(recipients),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    pending = sorted(recipients - already_sent, key=str)
                    if not pending:
                        continue

                    when = _event_time(event)
                    location = event.location or (
                        "Online"
                        if event.location_type == "online"
                        else "Venue to be confirmed"
                    )
                    label = _offset_label(hours)
                    body = f"{event.title} starts in {label}, on {when}. Location: {location}."
                    result = await dispatch_notification(
                        type="event_reminder",
                        category="events",
                        member_ids=[str(member_id) for member_id in pending],
                        title=f"Reminder: {event.title} is in {label}",
                        body=body,
                        action_url=f"/community/events/{event.id}",
                        icon="calendar",
                        metadata={
                            "event_id": str(event.id),
                            "reminder_hours": hours,
                        },
                        channels=["in_app", "email"],
                        email_template="generic_event_reminder",
                        email_data={
                            "body": body,
                            "html_content": (
                                f"<p>{escape(event.title)} starts in <strong>{escape(label)}</strong>.</p>"
                                f"<p>{escape(when)}<br>{escape(location)}</p>"
                            ),
                        },
                        expires_at=(event.end_time or event.start_time).isoformat(),
                        calling_service="events",
                    )
                    if result is None:
                        continue
                    for member_id in pending:
                        db.add(
                            EventReminderLog(
                                event_id=event.id,
                                member_id=member_id,
                                reminder_hours=hours,
                            )
                        )
                    await db.commit()
                    sent += len(pending)
            logger.info("Sent %s event reminder notification(s)", sent)
        except Exception:
            logger.exception("Event reminder processing failed")
            await db.rollback()
        finally:
            await db.close()
            break
