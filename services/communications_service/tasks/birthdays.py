"""Daily birthday-celebration task.

Runs from the ARQ worker at 06:00 UTC (~07:00 WAT). Pulls today's birthday
members from members_service, sends a birthday email to adults who haven't
opted out, creates an in-app notification for each, and posts a single
admin reminder so a human can do the WhatsApp shoutout.
"""

from uuid import UUID

from sqlalchemy import select

from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    get_admin_members,
    get_birthdays_today,
)
from libs.db.session import get_async_db
from services.communications_service.models import (
    Notification,
    NotificationPreferences,
)
from services.communications_service.templates.birthdays import send_birthday_email

logger = get_logger(__name__)

# Cutoff for sending the celebratory email directly to the member.
# Under-18s are still listed in the admin's WhatsApp reminder.
ADULT_CUTOFF_AGE = 18


async def send_daily_birthday_celebrations() -> None:
    """Send today's birthday emails + post the admin WhatsApp reminder."""
    members = await get_birthdays_today(calling_service="communications")

    if not members:
        logger.info("No birthdays today")
        return

    adults = [m for m in members if m.get("age", 0) >= ADULT_CUTOFF_AGE]

    async for db in get_async_db():
        try:
            sent_emails = 0
            celebrated_member_ids: list[str] = []

            if adults:
                adult_uuids = [UUID(m["id"]) for m in adults]
                prefs_result = await db.execute(
                    select(NotificationPreferences).where(
                        NotificationPreferences.member_id.in_(adult_uuids)
                    )
                )
                prefs_map = {str(p.member_id): p for p in prefs_result.scalars().all()}

                for m in adults:
                    pref = prefs_map.get(m["id"])
                    if pref is not None and not pref.email_birthday:
                        continue

                    try:
                        success = await send_birthday_email(
                            to_email=m["email"],
                            member_name=m["first_name"],
                        )
                    except Exception:
                        logger.exception(
                            "Failed to send birthday email to %s", m["email"]
                        )
                        success = False

                    if success:
                        sent_emails += 1
                        celebrated_member_ids.append(m["id"])

                        db.add(
                            Notification(
                                member_id=UUID(m["id"]),
                                type="birthday",
                                category="announcements",
                                title="Happy birthday! 🎉",
                                body=(
                                    "Wishing you an amazing year ahead "
                                    "from the SwimBuddz family."
                                ),
                                icon="cake",
                            )
                        )

                await db.commit()

            # Admin WhatsApp-shoutout reminder. Lists EVERYONE with a birthday
            # today (including minors) so the human chooses how to celebrate.
            try:
                admins = await get_admin_members(calling_service="communications")
            except Exception:
                logger.exception("Failed to fetch admins for birthday reminder")
                admins = []

            if admins and members:
                names = [m["first_name"] for m in members]
                joined_names = ", ".join(names)
                count = len(names)
                title = (
                    f"{count} birthday today — post in WhatsApp"
                    if count == 1
                    else f"{count} birthdays today — post in WhatsApp"
                )
                body = f"Today's birthdays: {joined_names}"

                await dispatch_notification(
                    type="birthday_admin_reminder",
                    category="announcements",
                    member_ids=[a["id"] for a in admins],
                    title=title,
                    body=body,
                    icon="cake",
                    action_url="/admin/communications/birthdays",
                    metadata={
                        "birthday_count": count,
                        "celebrated_member_ids": celebrated_member_ids,
                        "all_birthday_member_ids": [m["id"] for m in members],
                    },
                    calling_service="communications",
                )

            logger.info(
                "Birthday cron: %d total, %d emails sent, %d admin recipients",
                len(members),
                sent_emails,
                len(admins),
            )

        except Exception:
            logger.exception("Error in send_daily_birthday_celebrations")
            await db.rollback()
        finally:
            await db.close()
            break
