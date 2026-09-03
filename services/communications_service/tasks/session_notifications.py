"""
Background tasks for session notifications.

Handles:
- Scheduling notifications when sessions are published
- Sending booking prompts to eligible members who have not booked
- Processing pending notifications (reminders)
- Cancelling notifications when sessions are cancelled
"""

from datetime import date, datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_urls
from libs.common.service_client import (
    check_club_access_batch,
    dispatch_notification,
    get_confirmed_booking_member_ids,
    get_members_bulk,
    get_pod_by_id,
    get_session_by_id,
    internal_get,
)
from libs.common.session_access import (
    default_booking_prompt_tier as shared_default_booking_prompt_tier,
)
from libs.common.session_access import evaluate_session_access, member_declared_tiers
from libs.common.session_access import (
    has_any_paid_entitlement as shared_has_any_paid_entitlement,
)
from libs.common.session_access import (
    has_paid_session_access as shared_has_paid_session_access,
)
from libs.common.session_access import (
    is_unpaid_community_prospect as shared_is_unpaid_community_prospect,
)
from libs.db.session import get_async_db
from services.communications_service.models import (
    ContentPost,
    NotificationPreferences,
    ScheduledNotification,
    ScheduledNotificationStatus,
    SessionNotificationLog,
    SessionNotificationType,
    WeeklyDigestDispatch,
)
from services.communications_service.services.session_email_context import (
    build_session_email_contexts,
    with_booking_state,
)
from services.communications_service.tasks.content_publishing import (
    _member_can_read_post,
)
from services.communications_service.templates.session_notifications import (
    send_session_announcement_email,
    send_session_cancelled_email,
    send_session_prospect_invite_email,
    send_session_reminder_email,
)

logger = get_logger(__name__)

# Short notice threshold in hours
SHORT_NOTICE_THRESHOLD_HOURS = 6
BOOKING_PROMPT_WINDOW_DAYS = 7
BOOKING_PROMPT_MAX_SENDS_PER_SESSION = 3
BOOKING_PROMPT_SESSION_TYPES = {"community", "club", "cohort_class"}
BOOKING_PROMPT_TZ = ZoneInfo("Africa/Lagos")


async def _get_session_data(session_id: UUID) -> Optional[dict]:
    """Get session data from sessions-service."""
    return await get_session_by_id(str(session_id), calling_service="communications")


def _parse_session_start(session: dict) -> datetime:
    return datetime.fromisoformat(session["starts_at"])


def _parse_session_end(session: dict) -> datetime:
    return datetime.fromisoformat(session["ends_at"])


def _local_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=BOOKING_PROMPT_TZ)
    return value.astimezone(BOOKING_PROMPT_TZ).date()


def _is_booking_prompt_session(session: dict) -> bool:
    return (
        str(session.get("session_type") or "").lower() in BOOKING_PROMPT_SESSION_TYPES
    )


async def _get_active_members() -> list[dict]:
    settings = get_settings()
    members_resp = await internal_get(
        service_url=settings.MEMBERS_SERVICE_URL,
        path="/internal/members/active",
        calling_service="communications",
    )
    if members_resp.status_code != 200:
        logger.error("Failed to get active members for session booking prompt")
        return []
    return members_resp.json()


async def _sent_session_notification_recently(
    db: AsyncSession,
    *,
    session_id: UUID,
    member_id: UUID,
    now: datetime,
    notification_type: SessionNotificationType,
) -> bool:
    sent_times = (
        (
            await db.execute(
                select(SessionNotificationLog.sent_at)
                .where(
                    SessionNotificationLog.session_id == session_id,
                    SessionNotificationLog.member_id == member_id,
                    SessionNotificationLog.notification_type == notification_type,
                )
                .order_by(SessionNotificationLog.sent_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if len(sent_times) >= BOOKING_PROMPT_MAX_SENDS_PER_SESSION:
        return True

    today = _local_date(now)
    return any(_local_date(sent_at) == today for sent_at in sent_times)


def _has_any_paid_entitlement(member: dict, now: datetime) -> bool:
    return shared_has_any_paid_entitlement(member, now)


def _default_booking_prompt_tier(member: dict, now: datetime) -> str:
    """Return the member's default session-prompt tier without inherited access."""
    return shared_default_booking_prompt_tier(member, now)


def _has_paid_session_access(member: dict, session_type: str, now: datetime) -> bool:
    """Return whether a member should get a direct booking prompt."""
    return shared_has_paid_session_access(member, session_type, now)


def _is_unpaid_community_prospect(member: dict, now: datetime) -> bool:
    return shared_is_unpaid_community_prospect(member, now)


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
    Send booking prompt notifications to subscribed, unbooked members.

    Args:
        session_id: UUID of the published session.
        short_notice_message: Optional message explaining short notice.
    """
    async for db in get_async_db():
        try:
            # Get session via sessions-service
            session = await _get_session_data(session_id)
            if not session:
                logger.error(f"Session {session_id} not found for booking prompt")
                return

            all_members = await _get_active_members()
            sent_count = await _send_booking_prompt_for_session(
                db,
                session=session,
                active_members=all_members,
                short_notice_message=short_notice_message,
            )

            await db.commit()
            logger.info(
                "Sent session booking prompt to %s member(s) for session %s",
                sent_count,
                session_id,
            )

        except Exception as e:
            logger.error(f"Error sending session booking prompt: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def send_session_booking_prompts() -> None:
    """Send scheduled booking prompts for upcoming sessions to unbooked members.

    The ARQ worker runs this on Tuesday, Thursday, and Friday. It catches
    cohort sessions created before students enrolled, and follows up for any
    community/club/cohort session the member still has not booked.
    """
    async for db in get_async_db():
        try:
            now = utc_now()
            window_end = now + timedelta(days=BOOKING_PROMPT_WINDOW_DAYS)
            settings = get_settings()
            sessions_resp = await internal_get(
                service_url=settings.SESSIONS_SERVICE_URL,
                path="/internal/sessions/scheduled",
                calling_service="communications",
                params={
                    "start_date": now.isoformat(),
                    "end_date": window_end.isoformat(),
                },
            )
            if sessions_resp.status_code != 200:
                logger.error("Failed to get scheduled sessions for booking prompts")
                return

            sessions = [
                s for s in sessions_resp.json() if _is_booking_prompt_session(s)
            ]
            if not sessions:
                logger.info("No upcoming sessions require booking prompts")
                return

            all_members = await _get_active_members()
            email_contexts = await build_session_email_contexts(
                db,
                sessions,
                known_people=all_members,
            )
            total_sent = 0
            for session in sessions:
                total_sent += await _send_booking_prompt_for_session(
                    db,
                    session=session,
                    active_members=all_members,
                    send_prospect_invites=True,
                    email_context=email_contexts.sessions.get(str(session["id"])),
                    is_follow_up=True,
                )

            await db.commit()
            logger.info(
                "Sent %s booking prompt email(s) across %s upcoming session(s)",
                total_sent,
                len(sessions),
            )

        except Exception as e:
            logger.error(f"Error sending session booking prompts: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def _send_booking_prompt_for_session(
    db: AsyncSession,
    *,
    session: dict,
    active_members: list[dict],
    short_notice_message: str = "",
    send_prospect_invites: bool = False,
    email_context: dict | None = None,
    is_follow_up: bool = False,
) -> int:
    session_id = UUID(session["id"])
    now = utc_now()
    session_start = _parse_session_start(session)
    is_short_notice = (session_start - now).total_seconds() < (
        SHORT_NOTICE_THRESHOLD_HOURS * 3600
    )

    accessible_members = await _get_session_announcement_members(
        session=session,
        active_members=active_members,
    )
    if not accessible_members:
        return 0

    try:
        booked_ids = set(
            await get_confirmed_booking_member_ids(
                str(session_id),
                calling_service="communications",
            )
        )
    except Exception as exc:
        logger.error(
            "Failed to get confirmed bookings for booking prompt session %s: %s",
            session_id,
            exc,
        )
        return 0
    prefs_map = await _get_notification_preferences_by_auth(db, accessible_members)

    if email_context is None:
        context_batch = await build_session_email_contexts(
            db,
            [session],
            known_people=[*active_members, *accessible_members],
        )
        email_context = context_batch.sessions[str(session_id)]
    email_context = with_booking_state(email_context, is_booked=False)

    sent_member_ids: list[str] = []
    prospect_member_ids: list[str] = []
    sent_count = 0
    prospect_sent_count = 0
    session_type = str(session["session_type"])
    for member in accessible_members:
        member_id = member.get("id")
        if not member_id or member_id in booked_ids:
            continue
        pref = prefs_map.get(member.get("auth_id"))
        if not _should_send_session_announcement(session, pref):
            continue
        if not _has_paid_session_access(member, session_type, now):
            if (
                send_prospect_invites
                and session_type == "community"
                and _is_unpaid_community_prospect(member, now)
            ):
                if await _sent_session_notification_recently(
                    db,
                    session_id=session_id,
                    member_id=UUID(member_id),
                    now=now,
                    notification_type=SessionNotificationType.SPOTS_AVAILABLE,
                ):
                    continue
                try:
                    success = await send_session_prospect_invite_email(
                        to_email=member["email"],
                        member_name=member["first_name"],
                        session=email_context,
                    )

                    if success:
                        db.add(
                            SessionNotificationLog(
                                session_id=session_id,
                                member_id=UUID(member_id),
                                notification_type=SessionNotificationType.SPOTS_AVAILABLE,
                                channel="email",
                                delivery_status="sent",
                            )
                        )
                        prospect_member_ids.append(member_id)
                        prospect_sent_count += 1
                except Exception as e:
                    logger.error(
                        "Failed to send prospect session invite to %s: %s",
                        member["email"],
                        e,
                    )
            continue
        if await _sent_session_notification_recently(
            db,
            session_id=session_id,
            member_id=UUID(member_id),
            now=now,
            notification_type=SessionNotificationType.SESSION_PUBLISHED,
        ):
            continue

        try:
            success = await send_session_announcement_email(
                to_email=member["email"],
                member_name=member["first_name"],
                session=email_context,
                is_short_notice=is_short_notice,
                short_notice_message=short_notice_message,
                is_follow_up=is_follow_up,
            )

            if success:
                db.add(
                    SessionNotificationLog(
                        session_id=session_id,
                        member_id=UUID(member_id),
                        notification_type=SessionNotificationType.SESSION_PUBLISHED,
                        channel="email",
                        delivery_status="sent",
                    )
                )
                sent_member_ids.append(member_id)
                sent_count += 1

        except Exception as e:
            logger.error("Failed to send booking prompt to %s: %s", member["email"], e)

    if sent_member_ids:
        short_notice_prefix = "⚡ " if is_short_notice else ""
        await dispatch_notification(
            type="session_booking_prompt",
            category="sessions",
            member_ids=sent_member_ids,
            title=f"{short_notice_prefix}Book Session: {session['title']}",
            body=(
                f"{email_context['date']} at {email_context['time']} "
                f"— {email_context['location']}"
            ),
            action_url=f"/sessions/{session['id']}/book",
            icon="calendar",
            metadata={
                "session_id": str(session_id),
                "session_type": session["session_type"],
            },
            calling_service="communications",
        )

    if prospect_member_ids:
        await dispatch_notification(
            type="community_session_prospect_invite",
            category="sessions",
            member_ids=prospect_member_ids,
            title=f"Choose your SwimBuddz path: {session['title']}",
            body=(
                f"{email_context['date']} at {email_context['time']} "
                f"— {email_context['location']}"
            ),
            action_url="/account/billing?required=community",
            icon="calendar",
            metadata={
                "session_id": str(session_id),
                "session_type": session["session_type"],
                "audience": "prospect",
            },
            calling_service="communications",
        )
        logger.info(
            "Sent %s prospect invite email(s) for community session %s",
            prospect_sent_count,
            session_id,
        )

    return sent_count


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

    # Skip if session has already started (e.g. worker was down and is catching up)
    session_start = datetime.fromisoformat(session["starts_at"])
    now = utc_now()
    if session_start <= now:
        notification.status = ScheduledNotificationStatus.CANCELLED
        notification.error_message = "Session already started — reminder too late"
        logger.warning(
            f"Skipping stale {notification.notification_type.value} for session "
            f"{notification.session_id} (started {session_start.isoformat()})"
        )
        return

    if notification.notification_type == SessionNotificationType.SESSION_PUBLISHED:
        sent_count = await _send_booking_prompt_for_session(
            db,
            session=session,
            active_members=await _get_active_members(),
        )
        notification.status = ScheduledNotificationStatus.SENT
        notification.sent_at = utc_now()
        logger.info(
            "Sent scheduled booking prompt to %s member(s) for session %s",
            sent_count,
            notification.session_id,
        )
        return

    # Determine recipients based on notification type
    if notification.notification_type == SessionNotificationType.REMINDER_1H:
        # 1h reminders go only to coaches
        members = await _get_session_coaches(session)
    else:
        # 24h and 3h reminders go to confirmed booked members and coaches
        members = await _get_session_attendees_and_coaches(db, session)

    reminder_type = notification.notification_type.value.replace("reminder_", "")

    context_batch = await build_session_email_contexts(
        db,
        [session],
        known_people=members,
    )
    email_context = context_batch.sessions[str(notification.session_id)]
    booked_member_ids = {
        str(member_id)
        for member_id in (session.get("confirmed_booking_member_ids") or [])
    }

    sent_count = 0
    for member in members:
        # Check preferences
        prefs = await _get_member_preferences(db, member)
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
            member_context = with_booking_state(
                email_context,
                is_booked=str(member["id"]) in booked_member_ids,
            )
            success = await send_session_reminder_email(
                to_email=member["email"],
                member_name=member["first_name"],
                session=member_context,
                reminder_type=reminder_type,
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

    # Dispatch in-app notifications for reminders
    reminder_member_ids = [m["id"] for m in members]
    if reminder_member_ids:
        reminder_labels = {"24h": "tomorrow", "3h": "in 3 hours", "1h": "in 1 hour"}
        time_label = reminder_labels.get(reminder_type, f"in {reminder_type}")
        await dispatch_notification(
            type=f"session_reminder_{reminder_type}",
            category="sessions",
            member_ids=reminder_member_ids,
            title=f"Reminder: {session['title']} {time_label}",
            body=f"{email_context['date']} at {email_context['time']}",
            action_url=f"/sessions/{session['id']}/book",
            icon="clock",
            metadata={
                "session_id": str(notification.session_id),
                "reminder_type": reminder_type,
            },
            calling_service="communications",
        )

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

            # Send cancellation emails to confirmed booked members and coaches
            members = await _get_session_attendees_and_coaches(db, session)

            session_start = datetime.fromisoformat(session["starts_at"])
            local_tz = ZoneInfo(session.get("timezone", "Africa/Lagos"))
            local_start = session_start.astimezone(local_tz)
            session_date = local_start.strftime("%A, %B %d, %Y")
            session_time = local_start.strftime("%I:%M %p")

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

            # Dispatch in-app notifications for cancellation
            cancel_member_ids = [m["id"] for m in members]
            if cancel_member_ids:
                await dispatch_notification(
                    type="session_cancelled",
                    category="sessions",
                    member_ids=cancel_member_ids,
                    title=f"Session Cancelled: {session['title']}",
                    body=f"{session_date} at {session_time}"
                    + (f" — {cancellation_reason}" if cancellation_reason else ""),
                    action_url="/sessions",
                    icon="x-circle",
                    metadata={
                        "session_id": str(session_id),
                        "cancellation_reason": cancellation_reason,
                    },
                    calling_service="communications",
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
    _db: AsyncSession, session: dict
) -> list[dict]:
    """
    Get all members who should receive session notifications:
    - Confirmed booked members
    - Assigned coaches
    """
    # Get coaches via sessions-service
    coaches = await _get_session_coaches(session)
    coach_id_set = {m["id"] for m in coaches}

    try:
        booked_member_ids = await get_confirmed_booking_member_ids(
            session["id"],
            calling_service="communications",
        )
    except Exception as exc:
        logger.error(
            "Failed to get confirmed bookings for session %s: %s",
            session.get("id"),
            exc,
        )
        booked_member_ids = []

    unique_attendee_ids = [mid for mid in booked_member_ids if mid not in coach_id_set]
    attendees = (
        await get_members_bulk(unique_attendee_ids, calling_service="communications")
        if unique_attendee_ids
        else []
    )

    # Combine and deduplicate
    seen: set[str] = set()
    all_members = []
    for member in coaches + attendees:
        member_id = member.get("id")
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        all_members.append(member)
    return all_members


def _member_tiers(member: dict) -> set[str]:
    """Return normalized access tiers from an internal member payload."""
    return member_declared_tiers(member)


async def _get_session_announcement_members(
    *,
    session: dict,
    active_members: list[dict],
) -> list[dict]:
    """Return members allowed to receive a new-session booking prompt."""
    session_type = session.get("session_type")
    now = utc_now()

    async def club_access_for(members: list[dict]) -> dict[str, dict]:
        session_start = session.get("starts_at") or now.isoformat()
        return await check_club_access_batch(
            [
                {
                    "context_key": str(member["id"]),
                    "member_id": str(member["id"]),
                    "at": session_start,
                    "pool_id": session.get("pool_id"),
                    "pod_id": session.get("pod_id"),
                }
                for member in members
                if member.get("id")
            ],
            calling_service="communications",
        )

    if session_type == "cohort_class":
        cohort_id = session.get("cohort_id")
        if not cohort_id:
            return []
        settings = get_settings()
        resp = await internal_get(
            service_url=settings.ACADEMY_SERVICE_URL,
            path=f"/internal/academy/cohorts/{cohort_id}/enrolled-students",
            calling_service="communications",
        )
        if resp.status_code != 200:
            logger.error(
                "Failed to get enrolled students for cohort session %s",
                session.get("id"),
            )
            return []
        enrollment_by_member = {
            str(row["member_id"]): {
                "enrolled": True,
                "status": row.get("status"),
                "access_suspended": bool(row.get("access_suspended")),
            }
            for row in resp.json()
            if row.get("member_id")
        }
        cohort_members = await get_members_bulk(
            list(enrollment_by_member), calling_service="communications"
        )
        return [
            member
            for member in cohort_members
            if evaluate_session_access(
                member,
                session,
                now=now,
                cohort_enrollment=enrollment_by_member.get(str(member.get("id"))),
            ).prompt_eligible
        ]

    if session_type == "club" and session.get("pod_id"):
        pod = await get_pod_by_id(session["pod_id"], calling_service="communications")
        if not pod:
            logger.error(
                "Failed to get pod %s for club session %s",
                session.get("pod_id"),
                session.get("id"),
            )
            return []
        pod_members = await get_members_bulk(
            pod.get("active_member_ids") or [],
            calling_service="communications",
        )
        pod_member_ids = {
            str(member_id) for member_id in (pod.get("active_member_ids") or [])
        }
        club_access = await club_access_for(pod_members)
        return [
            member
            for member in pod_members
            if evaluate_session_access(
                member,
                session,
                now=now,
                pod_member_ids=pod_member_ids,
                club_product_access=bool(
                    (club_access.get(str(member.get("id"))) or {}).get("allowed")
                ),
            ).prompt_eligible
        ]

    if session_type in {"club", "community", "event"}:
        club_access = (
            await club_access_for(active_members) if session_type == "club" else {}
        )
        return [
            member
            for member in active_members
            if evaluate_session_access(
                member,
                session,
                now=now,
                club_product_access=(
                    bool(
                        (club_access.get(str(member.get("id"))) or {}).get("allowed")
                    )
                    if session_type == "club"
                    else None
                ),
            ).prompt_eligible
            or (
                session_type == "community"
                and _is_unpaid_community_prospect(member, now)
            )
        ]

    return []


async def _get_notification_preferences_by_auth(
    db: AsyncSession,
    members: list[dict],
) -> dict[str, NotificationPreferences]:
    auth_ids = [m["auth_id"] for m in members if m.get("auth_id")]
    if not auth_ids:
        return {}
    prefs_result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.member_auth_id.in_(auth_ids)
        )
    )
    return {p.member_auth_id: p for p in prefs_result.scalars().all()}


def _should_send_session_announcement(
    session: dict,
    pref: Optional[NotificationPreferences],
) -> bool:
    """Check booking-prompt preferences. Missing prefs default to opted-in."""
    if pref is None:
        return True
    if pref.email_session_reminders is False:
        return False

    session_type = session.get("session_type")
    if session_type == "community":
        return pref.subscribe_community_sessions
    if session_type == "club":
        return pref.subscribe_club_sessions
    if session_type == "event":
        return pref.subscribe_event_sessions
    if session_type in {"academy", "cohort_class"}:
        return pref.email_academy_updates
    return True


async def _get_member_preferences(
    db: AsyncSession, member: dict
) -> Optional[NotificationPreferences]:
    """Get notification preferences for a member."""
    auth_id = member.get("auth_id")
    if not auth_id:
        return None
    result = await db.execute(
        select(NotificationPreferences).where(
            NotificationPreferences.member_auth_id == auth_id
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
    """Send the idempotent, tier-sectioned weekly digest on Sundays."""
    from services.communications_service.templates.session_notifications import (
        send_weekly_session_digest_email,
    )

    async for db in get_async_db():
        try:
            now = utc_now()
            local_now = now.astimezone(BOOKING_PROMPT_TZ)
            days_until_monday = (7 - local_now.weekday()) % 7 or 7
            week_start_local = datetime.combine(
                local_now.date() + timedelta(days=days_until_monday),
                datetime.min.time(),
                tzinfo=BOOKING_PROMPT_TZ,
            )
            week_end_local = week_start_local + timedelta(days=7)
            week_start = week_start_local.astimezone(ZoneInfo("UTC"))
            week_end = week_end_local.astimezone(ZoneInfo("UTC"))

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

            week_label = (
                f"{week_start_local.strftime('%B %d')} - "
                f"{(week_end_local - timedelta(days=1)).strftime('%d, %Y')}"
            )
            campaign_key = f"week-{week_start_local.date().isoformat()}"

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

            prefs_map = await _get_notification_preferences_by_auth(db, all_members)
            articles_result = await db.execute(
                select(ContentPost)
                .where(
                    ContentPost.is_published.is_(True),
                    ContentPost.published_at.isnot(None),
                    ContentPost.published_at >= (now - timedelta(days=7)),
                    ContentPost.published_at <= now,
                )
                .order_by(ContentPost.published_at.desc())
                .limit(10)
            )
            recent_articles = articles_result.scalars().all()

            # Recognise a newly selected Volunteer of the Month in the first
            # weekly digest after selection. The winner receives a direct
            # congratulations email from volunteer_service; the digest is the
            # lower-noise community announcement and can include their photo.
            volunteer_spotlight = None
            try:
                spotlight_resp = await internal_get(
                    service_url=settings.VOLUNTEER_SERVICE_URL,
                    path="/volunteers/spotlight",
                    calling_service="communications",
                )
                if spotlight_resp.status_code == 200:
                    featured = spotlight_resp.json().get("featured_volunteer")
                    if featured and featured.get("featured_from"):
                        featured_from = datetime.fromisoformat(
                            str(featured["featured_from"])
                        )
                        if featured_from.tzinfo is None:
                            featured_from = featured_from.replace(
                                tzinfo=ZoneInfo("UTC")
                            )
                        if now - timedelta(days=7) <= featured_from <= now:
                            volunteer_spotlight = featured
            except Exception as exc:
                logger.warning("Digest volunteer spotlight lookup failed: %s", exc)

            if not sessions and not recent_articles and not volunteer_spotlight:
                logger.info(
                    "No sessions, articles, or volunteer recognition for weekly digest"
                )
                return

            email_contexts = await build_session_email_contexts(
                db,
                sessions,
                known_people=all_members,
            )
            digest_configs = email_contexts.audience_configs
            media_urls = await resolve_media_urls(
                [article.featured_image_media_id for article in recent_articles]
            )

            cohort_enrollments_by_session: dict[str, dict[str, dict]] = {}
            pod_member_ids_by_session = email_contexts.pod_member_ids_by_session
            for s in sessions:
                session_id = str(s.get("id"))
                session_type = str(s.get("session_type") or "").lower()
                if session_type == "cohort_class":
                    cohort_id = s.get("cohort_id")
                    if not cohort_id:
                        cohort_enrollments_by_session[session_id] = {}
                        continue
                    enrolled_resp = await internal_get(
                        service_url=settings.ACADEMY_SERVICE_URL,
                        path=(
                            f"/internal/academy/cohorts/{cohort_id}"
                            "/enrolled-students"
                        ),
                        calling_service="communications",
                    )
                    if enrolled_resp.status_code == 200:
                        cohort_enrollments_by_session[session_id] = {
                            str(row["member_id"]): {
                                "enrolled": True,
                                "status": row.get("status"),
                                "access_suspended": bool(row.get("access_suspended")),
                            }
                            for row in enrolled_resp.json()
                            if row.get("member_id")
                        }
                    else:
                        logger.error(
                            "Failed to get enrolled students for digest session %s",
                            session_id,
                        )
                        cohort_enrollments_by_session[session_id] = {}

            eligible_members = []
            for m in all_members:
                pref = prefs_map.get(m.get("auth_id"))
                wants_session_digest = pref is None or bool(pref.weekly_session_digest)
                wants_content_digest = not (pref and pref.weekly_digest is False)
                if not wants_session_digest and not wants_content_digest:
                    continue
                eligible_members.append(m)

            digest_club_access = await check_club_access_batch(
                [
                    {
                        "context_key": f"{member['id']}:{session['id']}",
                        "member_id": str(member["id"]),
                        "at": session["starts_at"],
                        "pool_id": session.get("pool_id"),
                        "pod_id": session.get("pod_id"),
                    }
                    for member in eligible_members
                    if member.get("id")
                    for session in sessions
                    if str(session.get("session_type") or "").lower() == "club"
                ],
                calling_service="communications",
            )

            sent_count = 0
            for member in eligible_members:
                member_sessions = []
                member_articles = []
                member_id = str(member.get("id"))
                pref = prefs_map.get(member.get("auth_id"))
                wants_session_digest = bool(pref and pref.weekly_session_digest)
                if pref is None:
                    wants_session_digest = True
                wants_content_digest = not (pref and pref.weekly_digest is False)
                member_volunteer_spotlight = (
                    volunteer_spotlight if wants_content_digest else None
                )
                if wants_session_digest:
                    for s in sessions:
                        session_id = str(s.get("id"))
                        booked_member_ids = {
                            str(value)
                            for value in s.get("confirmed_booking_member_ids") or []
                        }
                        is_booked = member_id in booked_member_ids
                        cohort_enrollment = None
                        if session_id in cohort_enrollments_by_session:
                            cohort_enrollment = cohort_enrollments_by_session[
                                session_id
                            ].get(
                                member_id,
                                {
                                    "enrolled": False,
                                    "access_suspended": False,
                                },
                            )
                        pod_member_ids = pod_member_ids_by_session.get(session_id)
                        access = evaluate_session_access(
                            member,
                            s,
                            now=now,
                            cohort_enrollment=cohort_enrollment,
                            pod_member_ids=pod_member_ids,
                            confirmed_booking=is_booked,
                            club_product_access=(
                                bool(
                                    (
                                        digest_club_access.get(
                                            f"{member_id}:{session_id}"
                                        )
                                        or {}
                                    ).get("allowed")
                                )
                                if str(s.get("session_type") or "").lower()
                                == "club"
                                else None
                            ),
                        )
                        if not access.digest_eligible:
                            continue

                        base_context = email_contexts.sessions[session_id]
                        if not base_context["audience_enabled"]:
                            continue
                        member_context = with_booking_state(
                            base_context,
                            is_booked=is_booked,
                        )
                        member_context["date"] = base_context["digest_date"]
                        member_context["time"] = base_context["time_range"]
                        member_sessions.append(member_context)

                if wants_content_digest:
                    for article in recent_articles:
                        if not _member_can_read_post(member, article):
                            continue
                        member_articles.append(
                            {
                                "id": str(article.id),
                                "title": article.title,
                                "summary": article.summary,
                                "category": article.category,
                                "image_url": media_urls.get(
                                    article.featured_image_media_id
                                ),
                            }
                        )
                        if len(member_articles) >= 2:
                            break

                if (
                    not member_sessions
                    and not member_articles
                    and not member_volunteer_spotlight
                ):
                    continue

                dispatch = (
                    await db.execute(
                        select(WeeklyDigestDispatch).where(
                            WeeklyDigestDispatch.campaign_key == campaign_key,
                            WeeklyDigestDispatch.member_id == UUID(member_id),
                        )
                    )
                ).scalar_one_or_none()
                if dispatch and dispatch.delivery_status in {
                    "sent",
                    "sending",
                    "unknown",
                }:
                    continue
                if dispatch is None:
                    dispatch = WeeklyDigestDispatch(
                        campaign_key=campaign_key,
                        member_id=UUID(member_id),
                        recipient_email=member["email"],
                        tracking_token=uuid4(),
                        delivery_status="pending",
                        attempt_count=0,
                        click_count=0,
                    )
                    db.add(dispatch)
                    await db.flush()

                public_api_url = settings.PUBLIC_API_URL or (
                    "https://api.swimbuddz.com"
                    if settings.ENVIRONMENT == "production"
                    else settings.GATEWAY_URL
                )
                tracking_base = (
                    f"{public_api_url.rstrip('/')}"
                    f"/api/v1/communications/digest/click/{dispatch.tracking_token}"
                )
                for session in member_sessions:
                    kind = "session-manage" if session["is_booked"] else "session"
                    session["action_url"] = f"{tracking_base}/{kind}/{session['id']}"
                for article in member_articles:
                    article["url"] = f"{tracking_base}/article/{article['id']}"
                preferences_url = f"{tracking_base}/preferences/me"

                dispatch.delivery_status = "sending"
                dispatch.attempt_count += 1
                dispatch.error_message = None
                await db.commit()

                try:
                    success = await send_weekly_session_digest_email(
                        to_email=member["email"],
                        member_name=member["first_name"],
                        week_label=week_label,
                        sessions=member_sessions,
                        articles=member_articles,
                        digest_configs=digest_configs,
                        preferences_url=preferences_url,
                        volunteer_spotlight=member_volunteer_spotlight,
                    )
                    if success:
                        dispatch.delivery_status = "sent"
                        dispatch.sent_at = utc_now()
                        sent_count += 1
                    else:
                        dispatch.delivery_status = "failed"
                        dispatch.error_message = "Email provider returned failure"
                except Exception as e:
                    dispatch.delivery_status = "failed"
                    dispatch.error_message = str(e)[:2000]
                    logger.error(
                        f"Failed to send weekly digest to {member['email']}: {e}"
                    )
                finally:
                    await db.commit()

            logger.info(f"Sent weekly session digest to {sent_count} members")

        except Exception as e:
            logger.error(f"Error sending weekly session digest: {e}")
        finally:
            await db.close()
            break
