"""Background tasks for academy service automation."""

import asyncio
from datetime import timedelta

from libs.common.datetime_utils import utc_now
from libs.common.email import send_enrollment_reminder_email
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
)
from services.members_service.models import Member
from sqlalchemy import select

logger = get_logger(__name__)


async def send_enrollment_reminders():
    """
    Send reminders for upcoming cohorts:
    - 7 days before (General)
    - 3 days before (Logistics)
    - 1 day before (Urgent)
    """
    async for db in get_async_db():
        try:
            now = utc_now()
            today = now.date()

            # Find active/open cohorts starting in next 8 days
            query = select(Cohort).where(
                Cohort.status.in_([CohortStatus.OPEN, CohortStatus.ACTIVE]),
                Cohort.start_date > now,
                Cohort.start_date <= now + timedelta(days=8),
            )
            result = await db.execute(query)
            cohorts = result.scalars().all()

            for cohort in cohorts:
                days_until = (cohort.start_date.date() - today).days

                # Only target 7, 3, or 1 days out
                if days_until not in [7, 3, 1]:
                    continue

                reminder_key = f"{days_until}_days"

                # Get enrolled students
                enrollment_query = (
                    select(Enrollment, Member)
                    .join(Member, Enrollment.member_id == Member.id)
                    .where(
                        Enrollment.cohort_id == cohort.id,
                        Enrollment.status == EnrollmentStatus.ENROLLED,
                    )
                )
                result = await db.execute(enrollment_query)
                enrollments = result.all()  # List of (Enrollment, Member) tuples

                for enrollment, member in enrollments:
                    # Check if already sent
                    reminders_sent = enrollment.reminders_sent or []
                    if reminder_key in reminders_sent:
                        continue

                    # Send email
                    success = await send_enrollment_reminder_email(
                        to_email=member.email,
                        member_name=member.first_name,
                        program_name=(
                            cohort.program.name if cohort.program else "Swimming Course"
                        ),
                        cohort_name=cohort.name,
                        start_date=cohort.start_date.strftime("%B %d, %Y"),
                        start_time=cohort.start_date.strftime("%I:%M %p"),
                        location=cohort.location_name or "TBD",
                        days_until=days_until,
                    )

                    if success:
                        # Update DB
                        new_reminders = reminders_sent + [reminder_key]
                        enrollment.reminders_sent = new_reminders
                        logger.info(
                            f"Sent {days_until}-day reminder to {member.email} for cohort {cohort.id}"
                        )
                    else:
                        logger.error(
                            f"Failed to send {days_until}-day reminder to {member.email}"
                        )

            await db.commit()

        except Exception as e:
            logger.error(f"Error sending enrollment reminders: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def transition_cohort_statuses():
    """
    Automatically transition cohort statuses based on dates:
    - OPEN → ACTIVE on start_date
    - ACTIVE → COMPLETED on end_date

    Should be run periodically (e.g., every hour via cron or scheduler).
    """
    async for db in get_async_db():
        try:
            now = utc_now()

            # Transition OPEN → ACTIVE for cohorts that have started
            open_query = select(Cohort).where(
                Cohort.status == CohortStatus.OPEN,
                Cohort.start_date <= now,
            )
            result = await db.execute(open_query)
            open_cohorts = result.scalars().all()

            for cohort in open_cohorts:
                cohort.status = CohortStatus.ACTIVE
                logger.info(
                    f"Transitioned cohort {cohort.id} ({cohort.name}) from OPEN to ACTIVE"
                )

            # Transition ACTIVE → COMPLETED for cohorts that have ended
            active_query = select(Cohort).where(
                Cohort.status == CohortStatus.ACTIVE,
                Cohort.end_date <= now,
            )
            result = await db.execute(active_query)
            active_cohorts = result.scalars().all()

            for cohort in active_cohorts:
                cohort.status = CohortStatus.COMPLETED
                logger.info(
                    f"Transitioned cohort {cohort.id} ({cohort.name}) from ACTIVE to COMPLETED"
                )

            await db.commit()

            total_transitions = len(open_cohorts) + len(active_cohorts)
            if total_transitions > 0:
                logger.info(
                    f"Cohort status transitions completed: {len(open_cohorts)} OPEN→ACTIVE, {len(active_cohorts)} ACTIVE→COMPLETED"
                )

        except Exception as e:
            logger.error(f"Error transitioning cohort statuses: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def run_periodic_tasks():
    """
    Run all periodic tasks in a loop.
    This can be started as a background process or via a task scheduler.
    """
    logger.info("Starting academy service periodic tasks...")

    while True:
        try:
            await transition_cohort_statuses()
            await send_enrollment_reminders()
        except Exception as e:
            logger.error(f"Error in periodic tasks: {e}")

        # Run every hour
        await asyncio.sleep(3600)


if __name__ == "__main__":
    # For manual testing or running as standalone process
    asyncio.run(send_enrollment_reminders())
