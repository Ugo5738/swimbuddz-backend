"""
Background tasks for session notifications.

Handles:
- Scheduling notifications when sessions are published
- Processing pending notifications (reminders)
- Cancelling notifications when sessions are cancelled
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_members_bulk, get_session_by_id, internal_get
from libs.db.session import get_async_db
from services.communications_service.models import (
    NotificationPreferences,
    ScheduledNotification,
    ScheduledNotificationStatus,
    SessionNotificationLog,
    SessionNotificationType,
)
from services.communications_service.templates.session_notifications import (
    send_session_announcement_email,
    send_session_cancelled_email,
    send_session_reminder_email,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Short notice threshold in hours
SHORT_NOTICE_THRESHOLD_HOURS = 6


async def _get_session_data(session_id: UUID) -> Optional[dict]:
    """Get session data from sessions-service."""
    return await get_session_by_id(str(session_id), calling_service="communications")


async def schedule_session_notifications(
    session_id: UUID,
    is_short_notice: bool = False,
) -> None:
    """
    Schedule reminder notifications for a newly published session.

    Creates ScheduledNotification entries for:
    - 24 hours before (if enough time)
    - 3 hours before (if enough time)
    - 1 hour before (coaches only, if enough time)

    Args:
        session_id: UUID of the published session.
        is_short_notice: Whether this was same-day/short notice creation.
    """
    async for db in get_async_db():
        try:
            # Get session details via sessions-service
            session = await _get_session_data(session_id)
            if not session:
                logger.error(
                    f"Session {session_id} not found for notification scheduling"
                )
                return

            now = utc_now()
            session_start = datetime.fromisoformat(session["starts_at"])

            # Calculate reminder times
            reminder_24h = session_start - timedelta(hours=24)
            reminder_3h = session_start - timedelta(hours=3)
            reminder_1h = session_start - timedelta(hours=1)

            notifications_to_create = []

            # 24h reminder - only if more than 24 hours away
            if reminder_24h > now:
                notifications_to_create.append(
                    ScheduledNotification(
                        session_id=session_id,
                        notification_type=SessionNotificationType.REMINDER_24H,
                        scheduled_for=reminder_24h,
                        status=ScheduledNotificationStatus.PENDING,
                        is_short_notice=is_short_notice,
                    )
                )

            # 3h reminder - only if more than 3 hours away
            if reminder_3h > now:
                notifications_to_create.append(
                    ScheduledNotification(
                        session_id=session_id,
                        notification_type=SessionNotificationType.REMINDER_3H,
                        scheduled_for=reminder_3h,
                        status=ScheduledNotificationStatus.PENDING,
                        is_short_notice=is_short_notice,
                    )
                )

            # 1h reminder - only if more than 1 hour away
            if reminder_1h > now:
                notifications_to_create.append(
                    ScheduledNotification(
                        session_id=session_id,
                        notification_type=SessionNotificationType.REMINDER_1H,
                        scheduled_for=reminder_1h,
                        status=ScheduledNotificationStatus.PENDING,
                        is_short_notice=is_short_notice,
                    )
                )

            # Bulk create
            for notification in notifications_to_create:
                db.add(notification)

            await db.commit()

            logger.info(
                f"Scheduled {len(notifications_to_create)} reminder notifications for session {session_id}"
            )

        except Exception as e:
            logger.error(f"Error scheduling session notifications: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def send_session_announcement(
    session_id: UUID,
    short_notice_message: str = "",
) -> None:
    """
    Send immediate announcement notifications to subscribed members.

    Args:
        session_id: UUID of the published session.
        short_notice_message: Optional message explaining short notice.
    """
    async for db in get_async_db():
        try:
            # Get session via sessions-service
            session = await _get_session_data(session_id)
            if not session:
                logger.error(f"Session {session_id} not found for announcement")
                return

            now = utc_now()
            session_start = datetime.fromisoformat(session["starts_at"])
            is_short_notice = (session_start - now).total_seconds() < (
                SHORT_NOTICE_THRESHOLD_HOURS * 3600
            )

            # Determine subscription field based on session type
            session_type_subscription_map = {
                "community": "subscribe_community_sessions",
                "club": "subscribe_club_sessions",
                "event": "subscribe_event_sessions",
            }
            subscription_field = session_type_subscription_map.get(
                session["session_type"], "subscribe_community_sessions"
            )

            # Get active members from members-service
            settings = get_settings()
            members_resp = await internal_get(
                service_url=settings.MEMBERS_SERVICE_URL,
                path="/internal/members/active",
                calling_service="communications",
            )
            if members_resp.status_code != 200:
                logger.error("Failed to get active members for announcement")
                return
            all_members = members_resp.json()

            # Filter by notification preferences (our own table)
            member_ids_with_prefs = {}
            for m in all_members:
                member_ids_with_prefs[m["id"]] = m

            # Get notification preferences for these members
            member_uuids = [UUID(m["id"]) for m in all_members]
            prefs_result = await db.execute(
                select(NotificationPreferences).where(
                    NotificationPreferences.member_id.in_(member_uuids)
                )
            )
            prefs_map = {str(p.member_id): p for p in prefs_result.scalars().all()}

            # Filter members based on preferences
            eligible_members = []
            for m in all_members:
                pref = prefs_map.get(m["id"])
                # Default: subscribed (None means opted-in)
                subscribed = True
                if pref:
                    sub_val = getattr(pref, subscription_field, None)
                    if sub_val is False:
                        subscribed = False
                    if pref.email_session_reminders is False:
                        subscribed = False
                if subscribed:
                    eligible_members.append(m)

            # Format session details
            session_date = session_start.strftime("%A, %B %d, %Y")
            session_time = session_start.strftime("%I:%M %p")

            sent_count = 0
            for member in eligible_members:
                # Check if already sent (prevent duplicates)
                existing_log = await db.execute(
                    select(SessionNotificationLog).where(
                        SessionNotificationLog.session_id == session_id,
                        SessionNotificationLog.member_id == UUID(member["id"]),
                        SessionNotificationLog.notification_type
                        == SessionNotificationType.SESSION_PUBLISHED,
                    )
                )
                if existing_log.scalar_one_or_none():
                    continue

                try:
                    success = await send_session_announcement_email(
                        to_email=member["email"],
                        member_name=member["first_name"],
                        session_title=session["title"],
                        session_type=session["session_type"],
                        session_date=session_date,
                        session_time=session_time,
                        session_location=session.get("location_name")
                        or session.get("location")
                        or "TBD",
                        session_address=session.get("location_address") or "",
                        pool_fee=session.get("pool_fee") or 0,
                        is_short_notice=is_short_notice,
                        short_notice_message=short_notice_message,
                    )

                    if success:
                        # Log the notification
                        log_entry = SessionNotificationLog(
                            session_id=session_id,
                            member_id=UUID(member["id"]),
                            notification_type=SessionNotificationType.SESSION_PUBLISHED,
                            channel="email",
                            delivery_status="sent",
                        )
                        db.add(log_entry)
                        sent_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to send announcement to {member['email']}: {e}"
                    )

            await db.commit()
            logger.info(
                f"Sent session announcement to {sent_count} members for session {session_id}"
            )

        except Exception as e:
            logger.error(f"Error sending session announcement: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def process_pending_notifications() -> None:
    """
    Process all pending scheduled notifications that are due.

    This is called periodically by the ARQ worker (every 5 minutes).
    """
    async for db in get_async_db():
        try:
            now = utc_now()

            # Find due notifications
            query = (
                select(ScheduledNotification)
                .where(
                    ScheduledNotification.status == ScheduledNotificationStatus.PENDING,
                    ScheduledNotification.scheduled_for <= now,
                )
                .order_by(ScheduledNotification.scheduled_for.asc())
                .limit(100)  # Process in batches
            )
            result = await db.execute(query)
            notifications = result.scalars().all()

            if not notifications:
                return

            logger.info(f"Processing {len(notifications)} pending notifications")

            for notification in notifications:
                try:
                    await _process_single_notification(db, notification)
                except Exception as e:
                    logger.error(
                        f"Error processing notification {notification.id}: {e}"
                    )
                    notification.status = ScheduledNotificationStatus.FAILED
                    notification.error_message = str(e)

            await db.commit()

        except Exception as e:
            logger.error(f"Error in process_pending_notifications: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def _process_single_notification(
    db: AsyncSession,
    notification: ScheduledNotification,
) -> None:
    """Process a single scheduled notification."""
    # Get session via sessions-service
    session = await _get_session_data(notification.session_id)
    if not session:
        notification.status = ScheduledNotificationStatus.CANCELLED
        notification.error_message = "Session not found"
        return

    # Skip if session is cancelled or completed
    if session["status"] in ["cancelled", "completed"]:
        notification.status = ScheduledNotificationStatus.CANCELLED
        notification.error_message = f"Session is {session['status']}"
        return

    # Determine recipients based on notification type
    if notification.notification_type == SessionNotificationType.REMINDER_1H:
        # 1h reminders go only to coaches
        members = await _get_session_coaches(session)
    else:
        # 24h and 3h reminders go to registered attendees and coaches
        members = await _get_session_attendees_and_coaches(db, session)

    reminder_type = notification.notification_type.value.replace("reminder_", "")

    # Format session details
    session_start = datetime.fromisoformat(session["starts_at"])
    session_date = session_start.strftime("%A, %B %d, %Y")
    session_time = session_start.strftime("%I:%M %p")

    sent_count = 0
    for member in members:
        # Check preferences
        prefs = await _get_member_preferences(db, UUID(member["id"]))
        if not _should_send_reminder(prefs, reminder_type):
            continue

        # Check if already sent
        existing = await db.execute(
            select(SessionNotificationLog).where(
                SessionNotificationLog.session_id == notification.session_id,
                SessionNotificationLog.member_id == UUID(member["id"]),
                SessionNotificationLog.notification_type
                == notification.notification_type,
            )
        )
        if existing.scalar_one_or_none():
            continue

        try:
            success = await send_session_reminder_email(
                to_email=member["email"],
                member_name=member["first_name"],
                session_title=session["title"],
                session_date=session_date,
                session_time=session_time,
                session_location=session.get("location_name")
                or session.get("location")
                or "TBD",
                session_address=session.get("location_address") or "",
                reminder_type=reminder_type,
                pool_fee=session.get("pool_fee") or 0,
            )

            if success:
                log_entry = SessionNotificationLog(
                    session_id=notification.session_id,
                    member_id=UUID(member["id"]),
                    notification_type=notification.notification_type,
                    channel="email",
                    delivery_status="sent",
                )
                db.add(log_entry)
                sent_count += 1

        except Exception as e:
            logger.error(f"Failed to send reminder to {member['email']}: {e}")

    notification.status = ScheduledNotificationStatus.SENT
    notification.sent_at = utc_now()
    logger.info(
        f"Sent {notification.notification_type.value} to {sent_count} members for session {notification.session_id}"
    )


async def cancel_session_notifications(
    session_id: UUID,
    cancellation_reason: str = "",
) -> None:
    """
    Cancel all pending notifications for a session and send cancellation notices.

    Args:
        session_id: UUID of the cancelled session.
        cancellation_reason: Optional reason for cancellation.
    """
    async for db in get_async_db():
        try:
            # Cancel all pending notifications
            pending_query = select(ScheduledNotification).where(
                ScheduledNotification.session_id == session_id,
                ScheduledNotification.status == ScheduledNotificationStatus.PENDING,
            )
            result = await db.execute(pending_query)
            pending = result.scalars().all()

            for notification in pending:
                notification.status = ScheduledNotificationStatus.CANCELLED
                notification.error_message = "Session cancelled"

            logger.info(
                f"Cancelled {len(pending)} pending notifications for session {session_id}"
            )

            # Get session details for cancellation email
            session = await _get_session_data(session_id)
            if not session:
                await db.commit()
                return

            # Send cancellation emails to registered attendees
            members = await _get_session_attendees_and_coaches(db, session)

            session_start = datetime.fromisoformat(session["starts_at"])
            session_date = session_start.strftime("%A, %B %d, %Y")
            session_time = session_start.strftime("%I:%M %p")

            sent_count = 0
            for member in members:
                try:
                    success = await send_session_cancelled_email(
                        to_email=member["email"],
                        member_name=member["first_name"],
                        session_title=session["title"],
                        session_date=session_date,
                        session_time=session_time,
                        cancellation_reason=cancellation_reason,
                    )

                    if success:
                        log_entry = SessionNotificationLog(
                            session_id=session_id,
                            member_id=UUID(member["id"]),
                            notification_type=SessionNotificationType.SESSION_CANCELLED,
                            channel="email",
                            delivery_status="sent",
                        )
                        db.add(log_entry)
                        sent_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to send cancellation to {member['email']}: {e}"
                    )

            await db.commit()
            logger.info(
                f"Sent cancellation notice to {sent_count} members for session {session_id}"
            )

        except Exception as e:
            logger.error(f"Error cancelling session notifications: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


# ─── Helper functions ─────────────────────────────────────────────────


async def _get_session_coaches(session: dict) -> list[dict]:
    """Get coach members for a session via sessions-service + members-service."""
    settings = get_settings()

    # Get coach IDs from sessions-service
    resp = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/{session['id']}/coaches",
        calling_service="communications",
    )
    if resp.status_code != 200:
        return []
    coach_ids = resp.json()

    if not coach_ids:
        return []

    # Bulk-lookup coach member details
    return await get_members_bulk(coach_ids, calling_service="communications")


async def _get_session_attendees_and_coaches(
    db: AsyncSession, session: dict
) -> list[dict]:
    """
    Get all members who should receive session notifications:
    - Registered attendees (from attendance_records — our local table if shared,
      otherwise via attendance-service)
    - Assigned coaches

    TODO: Once RSVP system is implemented, query from RSVPs instead.
    """
    settings = get_settings()

    # Get coaches via sessions-service
    coaches = await _get_session_coaches(session)
    coach_id_set = {m["id"] for m in coaches}

    # For community sessions, also get registered attendees via attendance-service
    attendees = []
    if session.get("session_type") == "community":
        att_resp = await internal_get(
            service_url=settings.ATTENDANCE_SERVICE_URL,
            path=f"/internal/attendance/session/{session['id']}/member-ids",
            calling_service="communications",
        )
        if att_resp.status_code == 200:
            attendee_ids = att_resp.json()
            # Remove duplicates with coaches
            unique_attendee_ids = [
                aid for aid in attendee_ids if aid not in coach_id_set
            ]
            if unique_attendee_ids:
                attendees = await get_members_bulk(
                    unique_attendee_ids, calling_service="communications"
                )

    # Combine and deduplicate
    all_members = coaches + attendees
    return all_members


async def _get_member_preferences(
    db: AsyncSession, member_id: UUID
) -> Optional[NotificationPreferences]:
    """Get notification preferences for a member."""
    result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.member_id == member_id
        )
    )
    return result.scalar_one_or_none()


def _should_send_reminder(
    prefs: Optional[NotificationPreferences], reminder_type: str
) -> bool:
    """Check if member should receive this reminder based on preferences."""
    if prefs is None:
        # Default: send all reminders
        return True

    if not prefs.email_session_reminders:
        return False

    if reminder_type == "24h" and not prefs.reminder_24h_enabled:
        return False

    if reminder_type == "3h" and not prefs.reminder_3h_enabled:
        return False

    # 1h reminders are always sent to coaches (no preference check)
    return True


async def send_weekly_session_digest() -> None:
    """
    Send weekly digest of upcoming sessions to members who opted in.

    Called by the ARQ worker on Sundays.
    """
    from services.communications_service.templates.session_notifications import (
        send_weekly_session_digest_email,
    )

    async for db in get_async_db():
        try:
            now = utc_now()
            week_start = now
            week_end = now + timedelta(days=7)

            # Get upcoming sessions for the week via sessions-service
            settings = get_settings()
            sessions_resp = await internal_get(
                service_url=settings.SESSIONS_SERVICE_URL,
                path="/internal/sessions/scheduled",
                calling_service="communications",
                params={
                    "start_date": week_start.isoformat(),
                    "end_date": week_end.isoformat(),
                },
            )
            if sessions_resp.status_code != 200:
                logger.error("Failed to get scheduled sessions for digest")
                return
            sessions = sessions_resp.json()

            if not sessions:
                logger.info("No sessions this week for digest")
                return

            # Format sessions for email
            sessions_list = [
                {
                    "title": s["title"],
                    "type": s["session_type"],
                    "date": datetime.fromisoformat(s["starts_at"]).strftime(
                        "%A, %B %d"
                    ),
                    "time": datetime.fromisoformat(s["starts_at"]).strftime("%I:%M %p"),
                    "location": s.get("location_name") or s.get("location") or "TBD",
                }
                for s in sessions
            ]

            week_label = (
                f"{week_start.strftime('%B %d')} - {week_end.strftime('%d, %Y')}"
            )

            # Get active members from members-service
            members_resp = await internal_get(
                service_url=settings.MEMBERS_SERVICE_URL,
                path="/internal/members/active",
                calling_service="communications",
            )
            if members_resp.status_code != 200:
                logger.error("Failed to get active members for digest")
                return
            all_members = members_resp.json()

            # Filter by weekly digest preference (our own table)
            member_uuids = [UUID(m["id"]) for m in all_members]
            prefs_result = await db.execute(
                select(NotificationPreferences).where(
                    NotificationPreferences.member_id.in_(member_uuids)
                )
            )
            prefs_map = {str(p.member_id): p for p in prefs_result.scalars().all()}

            eligible_members = []
            for m in all_members:
                pref = prefs_map.get(m["id"])
                # Default: opted-in (None means yes)
                if pref and pref.weekly_session_digest is False:
                    continue
                eligible_members.append(m)

            sent_count = 0
            for member in eligible_members:
                try:
                    success = await send_weekly_session_digest_email(
                        to_email=member["email"],
                        member_name=member["first_name"],
                        week_label=week_label,
                        sessions=sessions_list,
                    )
                    if success:
                        sent_count += 1
                except Exception as e:
                    logger.error(
                        f"Failed to send weekly digest to {member['email']}: {e}"
                    )

            logger.info(f"Sent weekly session digest to {sent_count} members")

        except Exception as e:
            logger.error(f"Error sending weekly session digest: {e}")
        finally:
            await db.close()
            break
