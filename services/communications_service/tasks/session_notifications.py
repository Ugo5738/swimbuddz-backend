"""
Background tasks for session notifications.

Handles:
- Scheduling notifications when sessions are published
- Sending booking prompts to eligible members who have not booked
- Processing pending notifications (reminders)
- Cancelling notifications when sessions are cancelled
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    get_confirmed_booking_member_ids,
    get_members_bulk,
    get_pod_by_id,
    get_session_by_id,
    internal_get,
)
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


def _session_fee_amount(session: dict) -> float:
    """Return the member-facing pool fee amount from an internal session payload.

    sessions-service returns pool_fee in kobo for internal calls; email templates
    render naira amounts.
    """
    raw_fee = session.get("pool_fee") or 0
    try:
        return float(raw_fee) / 100
    except (TypeError, ValueError):
        return 0


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


def _parse_paid_until(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _has_active_paid_until(member: dict, field: str, now: datetime) -> bool:
    paid_until = _parse_paid_until(member.get(field))
    return paid_until is not None and paid_until > now


def _has_any_paid_entitlement(member: dict, now: datetime) -> bool:
    return any(
        _has_active_paid_until(member, field, now)
        for field in (
            "community_paid_until",
            "club_paid_until",
            "academy_paid_until",
        )
    )


def _has_paid_session_access(member: dict, session_type: str, now: datetime) -> bool:
    """Return whether a member should get a direct booking prompt."""
    if session_type == "community":
        return _has_any_paid_entitlement(member, now)
    if session_type == "club":
        return _has_active_paid_until(
            member, "club_paid_until", now
        ) or _has_active_paid_until(member, "academy_paid_until", now)
    if session_type in {"academy", "cohort_class"}:
        # Cohort access is scoped by enrollment in _get_session_announcement_members.
        return True
    return _has_any_paid_entitlement(member, now)


def _is_unpaid_community_prospect(member: dict, now: datetime) -> bool:
    return "community" in _member_tiers(member) and not _has_any_paid_entitlement(
        member, now
    )


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
    """Send daily booking prompts for upcoming sessions to unbooked members.

    The ARQ worker runs this at the configured booking window. It catches
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
            total_sent = 0
            for session in sessions:
                total_sent += await _send_booking_prompt_for_session(
                    db,
                    session=session,
                    active_members=all_members,
                    send_prospect_invites=True,
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

    local_tz = ZoneInfo(session.get("timezone", "Africa/Lagos"))
    local_start = session_start.astimezone(local_tz)
    session_date = local_start.strftime("%A, %B %d, %Y")
    session_time = local_start.strftime("%I:%M %p")

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
                        session_id=str(session_id),
                        session_title=session["title"],
                        session_date=session_date,
                        session_time=session_time,
                        session_location=session.get("location_name")
                        or session.get("location")
                        or "TBD",
                        session_address=session.get("location_address") or "",
                        pool_fee=_session_fee_amount(session),
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
                session_id=str(session_id),
                session_title=session["title"],
                session_type=session["session_type"],
                session_date=session_date,
                session_time=session_time,
                session_location=session.get("location_name")
                or session.get("location")
                or "TBD",
                session_address=session.get("location_address") or "",
                pool_fee=_session_fee_amount(session),
                is_short_notice=is_short_notice,
                short_notice_message=short_notice_message,
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
        location_name = session.get("location_name") or session.get("location") or "TBD"
        await dispatch_notification(
            type="session_booking_prompt",
            category="sessions",
            member_ids=sent_member_ids,
            title=f"{short_notice_prefix}Book Session: {session['title']}",
            body=f"{session_date} at {session_time} — {location_name}",
            action_url=f"/sessions/{session['id']}/book",
            icon="calendar",
            metadata={
                "session_id": str(session_id),
                "session_type": session["session_type"],
            },
            calling_service="communications",
        )

    if prospect_member_ids:
        location_name = session.get("location_name") or session.get("location") or "TBD"
        await dispatch_notification(
            type="community_session_prospect_invite",
            category="sessions",
            member_ids=prospect_member_ids,
            title=f"Try SwimBuddz: {session['title']}",
            body=f"{session_date} at {session_time} — {location_name}",
            action_url="/checkout?purpose=community",
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

    # Format session details in the session's local timezone
    local_tz = ZoneInfo(session.get("timezone", "Africa/Lagos"))
    local_start = session_start.astimezone(local_tz)
    session_date = local_start.strftime("%A, %B %d, %Y")
    session_time = local_start.strftime("%I:%M %p")

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
            success = await send_session_reminder_email(
                to_email=member["email"],
                member_name=member["first_name"],
                session_id=str(notification.session_id),
                session_title=session["title"],
                session_date=session_date,
                session_time=session_time,
                session_location=session.get("location_name")
                or session.get("location")
                or "TBD",
                session_address=session.get("location_address") or "",
                reminder_type=reminder_type,
                pool_fee=_session_fee_amount(session),
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
            body=f"{session_date} at {session_time}",
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
    tiers = {
        str(t).lower() for t in (member.get("active_tiers") or []) if t is not None
    }
    primary = member.get("primary_tier")
    if primary:
        tiers.add(str(primary).lower())

    if "academy" in tiers:
        tiers.update({"club", "community"})
    if "club" in tiers:
        tiers.add("community")
    if not tiers:
        tiers.add("community")
    return tiers


async def _get_session_announcement_members(
    *,
    session: dict,
    active_members: list[dict],
) -> list[dict]:
    """Return members allowed to receive a new-session booking prompt."""
    session_type = session.get("session_type")

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
        member_ids = [row["member_id"] for row in resp.json() if row.get("member_id")]
        return await get_members_bulk(member_ids, calling_service="communications")

    if session_type == "club" and session.get("pod_id"):
        pod = await get_pod_by_id(session["pod_id"], calling_service="communications")
        if not pod:
            logger.error(
                "Failed to get pod %s for club session %s",
                session.get("pod_id"),
                session.get("id"),
            )
            return []
        return await get_members_bulk(
            pod.get("active_member_ids") or [],
            calling_service="communications",
        )

    if session_type == "club":
        return [m for m in active_members if "club" in _member_tiers(m)]

    if session_type == "academy":
        return [m for m in active_members if "academy" in _member_tiers(m)]

    if session_type == "community":
        return [m for m in active_members if "community" in _member_tiers(m)]

    # Events keep the previous broad active-member behavior, with preferences
    # applied below.
    return active_members


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

            # Format sessions for email (convert to local timezone)
            sessions_list = []
            for s in sessions:
                tz = ZoneInfo(s.get("timezone", "Africa/Lagos"))
                local_dt = datetime.fromisoformat(s["starts_at"]).astimezone(tz)
                sessions_list.append(
                    {
                        "title": s["title"],
                        "type": s["session_type"],
                        "date": local_dt.strftime("%A, %B %d"),
                        "time": local_dt.strftime("%I:%M %p"),
                        "location": s.get("location_name")
                        or s.get("location")
                        or "TBD",
                    }
                )

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

            prefs_map = await _get_notification_preferences_by_auth(db, all_members)

            eligible_members = []
            for m in all_members:
                pref = prefs_map.get(m.get("auth_id"))
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
