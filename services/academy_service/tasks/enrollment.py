"""Enrollment-related background tasks: reminders, waitlist, and cohort transitions."""

from datetime import timedelta

from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import get_members_bulk
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

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
            query = (
                select(Cohort)
                .options(selectinload(Cohort.program))
                .where(
                    Cohort.status.in_([CohortStatus.OPEN, CohortStatus.ACTIVE]),
                    Cohort.start_date > now,
                    Cohort.start_date <= now + timedelta(days=8),
                )
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
                enrollment_query = select(Enrollment).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                )
                result = await db.execute(enrollment_query)
                enrollment_list = result.scalars().all()

                # Bulk-lookup member details
                member_ids = list({str(e.member_id) for e in enrollment_list})
                members_data = await get_members_bulk(
                    member_ids, calling_service="academy"
                )
                members_map = {m["id"]: m for m in members_data}

                for enrollment in enrollment_list:
                    member = members_map.get(str(enrollment.member_id), {})
                    if not member:
                        continue

                    # Check if already sent
                    reminders_sent = enrollment.reminders_sent or []
                    if reminder_key in reminders_sent:
                        continue

                    # Send email via centralized email service
                    email_client = get_email_client()
                    success = await email_client.send_template(
                        template_type="enrollment_reminder",
                        to_email=member["email"],
                        template_data={
                            "member_name": member["first_name"],
                            "program_name": (
                                cohort.program.name
                                if cohort.program
                                else "Swimming Course"
                            ),
                            "cohort_name": cohort.name,
                            "start_date": cohort.start_date.strftime("%B %d, %Y"),
                            "start_time": cohort.start_date.strftime("%I:%M %p"),
                            "location": cohort.location_name or "TBD",
                            "days_until": days_until,
                        },
                    )

                    if success:
                        # Update DB
                        new_reminders = reminders_sent + [reminder_key]
                        enrollment.reminders_sent = new_reminders
                        logger.info(
                            f"Sent {days_until}-day reminder to {member['email']} for cohort {cohort.id}"
                        )
                    else:
                        logger.error(
                            f"Failed to send {days_until}-day reminder to {member['email']}"
                        )

            await db.commit()

        except Exception as e:
            logger.error(f"Error sending enrollment reminders: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def process_waitlist():
    """
    Check for open spots in active cohorts and promote waitlisted students.
    Logic:
    1. Find COHORTs where status is OPEN/ACTIVE and enrollment_count < capacity
    2. For each, find oldest WAITLIST enrollment
    3. Promote to PENDING_APPROVAL
    4. Send notification email
    """
    async for db in get_async_db():
        try:
            # Query cohorts that have space (capacity > enrolled_count)
            # Filter only OPEN/ACTIVE cohorts first.
            query = (
                select(Cohort)
                .options(selectinload(Cohort.program))
                .where(Cohort.status.in_([CohortStatus.OPEN, CohortStatus.ACTIVE]))
            )
            result = await db.execute(query)
            cohorts = result.scalars().all()

            for cohort in cohorts:
                # Count current enrollments
                count_query = select(func.count(Enrollment.id)).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                )
                result = await db.execute(count_query)
                enrolled_count = result.scalar() or 0

                if enrolled_count < cohort.capacity:
                    spots_available = cohort.capacity - enrolled_count

                    if spots_available > 0:
                        # Find oldest waitlisted students (FIFO)
                        waitlist_query = (
                            select(Enrollment)
                            .where(
                                Enrollment.cohort_id == cohort.id,
                                Enrollment.status == EnrollmentStatus.WAITLIST,
                            )
                            .order_by(Enrollment.created_at.asc())
                            .limit(spots_available)
                        )
                        result = await db.execute(waitlist_query)
                        to_promote = result.scalars().all()

                        # Bulk-lookup member details
                        wl_member_ids = [str(e.member_id) for e in to_promote]
                        wl_members = await get_members_bulk(
                            wl_member_ids, calling_service="academy"
                        )
                        wl_members_map = {m["id"]: m for m in wl_members}

                        for enrollment in to_promote:
                            member = wl_members_map.get(str(enrollment.member_id), {})
                            # Promote student
                            enrollment.status = EnrollmentStatus.PENDING_APPROVAL
                            logger.info(
                                f"Promoting user {member.get('email', 'unknown')} from waitlist for cohort {cohort.name}"
                            )

                            if member:
                                # Send email via centralized email service
                                email_client = get_email_client()
                                await email_client.send_template(
                                    template_type="waitlist_promotion",
                                    to_email=member["email"],
                                    template_data={
                                        "member_name": member["first_name"],
                                        "program_name": (
                                            cohort.program.name
                                            if cohort.program
                                            else "Swimming Course"
                                        ),
                                        "cohort_name": cohort.name,
                                    },
                                )

            await db.commit()

        except Exception as e:
            logger.error(f"Error processing waitlist: {e}")
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
