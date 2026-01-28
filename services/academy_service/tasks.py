"""Background tasks for academy service automation."""

import asyncio
from datetime import timedelta

from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
)
from services.members_service.models import Member
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

                    # Send email via centralized email service
                    email_client = get_email_client()
                    success = await email_client.send_template(
                        template_type="enrollment_reminder",
                        to_email=member.email,
                        template_data={
                            "member_name": member.first_name,
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
                            select(Enrollment, Member)
                            .join(Member, Enrollment.member_id == Member.id)
                            .where(
                                Enrollment.cohort_id == cohort.id,
                                Enrollment.status == EnrollmentStatus.WAITLIST,
                            )
                            .order_by(Enrollment.created_at.asc())
                            .limit(spots_available)
                        )
                        result = await db.execute(waitlist_query)
                        to_promote = result.all()  # List of (Enrollment, Member)

                        for enrollment, member in to_promote:
                            # Promote student
                            enrollment.status = EnrollmentStatus.PENDING_APPROVAL
                            logger.info(
                                f"Promoting user {member.email} from waitlist for cohort {cohort.name}"
                            )

                            # Send email via centralized email service
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="waitlist_promotion",
                                to_email=member.email,
                                template_data={
                                    "member_name": member.first_name,
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


async def send_weekly_progress_reports():
    """
    Send weekly progress report emails to all enrolled students in active cohorts.
    Includes PDF attachment with full progress details.
    Runs once per week (controlled by caller).
    """
    from datetime import timedelta

    from libs.common.pdf import generate_progress_report_pdf
    from services.academy_service.models import Milestone, StudentProgress

    async for db in get_async_db():
        try:
            now = utc_now()
            seven_days_ago = now - timedelta(days=7)

            # Find all active cohorts
            query = (
                select(Cohort)
                .options(selectinload(Cohort.program))
                .where(Cohort.status == CohortStatus.ACTIVE)
            )
            result = await db.execute(query)
            cohorts = result.scalars().all()

            for cohort in cohorts:
                program = cohort.program
                if not program:
                    continue

                # Get all enrollments for this cohort
                enrollment_query = (
                    select(Enrollment, Member)
                    .join(Member, Enrollment.member_id == Member.id)
                    .where(
                        Enrollment.cohort_id == cohort.id,
                        Enrollment.status == EnrollmentStatus.ENROLLED,
                    )
                )
                result = await db.execute(enrollment_query)
                enrollments = result.all()

                # Get total milestones for program
                milestone_query = select(Milestone).where(
                    Milestone.program_id == program.id
                )
                milestone_result = await db.execute(milestone_query)
                all_milestones = milestone_result.scalars().all()
                total_milestones = len(all_milestones)
                milestone_map = {m.id: m.name for m in all_milestones}

                for enrollment, member in enrollments:
                    # Get all progress for this enrollment
                    progress_query = select(StudentProgress).where(
                        StudentProgress.enrollment_id == enrollment.id
                    )
                    progress_result = await db.execute(progress_query)
                    all_progress = progress_result.scalars().all()

                    # Count completed milestones
                    completed = [
                        p for p in all_progress if p.status.value == "achieved"
                    ]
                    completed_count = len(completed)

                    # Recent achievements (last 7 days)
                    recent = [
                        {
                            "name": milestone_map.get(p.milestone_id, "Unknown"),
                            "achieved_at": p.achieved_at,
                        }
                        for p in completed
                        if p.achieved_at and p.achieved_at >= seven_days_ago
                    ]

                    # Coach feedback from recent reviews
                    feedback = [
                        {
                            "milestone": milestone_map.get(p.milestone_id, "Unknown"),
                            "notes": p.coach_notes,
                        }
                        for p in all_progress
                        if p.coach_notes
                        and p.reviewed_at
                        and p.reviewed_at >= seven_days_ago
                    ]

                    # Build milestone data for PDF
                    milestone_data = [
                        {
                            "name": milestone_map.get(p.milestone_id, "Unknown"),
                            "status": p.status.value if p.status else "pending",
                            "achieved_at": p.achieved_at,
                            "coach_notes": p.coach_notes,
                        }
                        for p in all_progress
                    ]

                    # Generate PDF
                    try:
                        generate_progress_report_pdf(
                            student_name=f"{member.first_name} {member.last_name}",
                            program_name=program.name,
                            cohort_name=cohort.name,
                            start_date=cohort.start_date,
                            end_date=cohort.end_date,
                            milestones=milestone_data,
                            total_milestones=total_milestones,
                            completed_milestones=completed_count,
                            report_date=now,
                        )
                    except Exception as pdf_err:
                        logger.error(
                            f"Failed to generate PDF for {member.email}: {pdf_err}"
                        )

                    # Send email via centralized email service
                    try:
                        email_client = get_email_client()
                        await email_client.send_template(
                            template_type="progress_report",
                            to_email=member.email,
                            template_data={
                                "member_name": member.first_name,
                                "program_name": program.name,
                                "cohort_name": cohort.name,
                                "milestones_completed": completed_count,
                                "total_milestones": total_milestones,
                                "recent_achievements": recent,
                                "coach_feedback": feedback,
                                # Note: PDF attachment not yet supported via API
                            },
                        )
                        logger.info(f"Sent progress report to {member.email}")
                    except Exception as email_err:
                        logger.error(
                            f"Failed to send progress report to {member.email}: {email_err}"
                        )

        except Exception as e:
            logger.error(f"Error sending weekly progress reports: {e}")
        finally:
            await db.close()
            break


async def check_and_issue_certificates():
    """
    Check for enrollments where all milestones are achieved but no certificate issued.
    Generate certificates and send emails.
    """
    import secrets

    from services.academy_service.models import Milestone, StudentProgress

    async for db in get_async_db():
        try:
            # Find active cohorts with their programs
            query = (
                select(Cohort)
                .options(selectinload(Cohort.program))
                .where(Cohort.status.in_([CohortStatus.ACTIVE, CohortStatus.COMPLETED]))
            )
            result = await db.execute(query)
            cohorts = result.scalars().all()

            for cohort in cohorts:
                program = cohort.program
                if not program:
                    continue

                # Get all milestones for this program
                milestone_query = select(Milestone).where(
                    Milestone.program_id == program.id
                )
                milestone_result = await db.execute(milestone_query)
                all_milestones = milestone_result.scalars().all()
                total_milestones = len(all_milestones)

                if total_milestones == 0:
                    continue  # No milestones defined

                # Get enrollments without certificates
                enrollment_query = (
                    select(Enrollment, Member)
                    .join(Member, Enrollment.member_id == Member.id)
                    .where(
                        Enrollment.cohort_id == cohort.id,
                        Enrollment.status == EnrollmentStatus.ENROLLED,
                        Enrollment.certificate_issued_at.is_(None),
                    )
                )
                enrollment_result = await db.execute(enrollment_query)
                enrollments = enrollment_result.all()

                for enrollment, member in enrollments:
                    # Count achieved milestones for this enrollment
                    progress_query = select(func.count(StudentProgress.id)).where(
                        StudentProgress.enrollment_id == enrollment.id,
                        StudentProgress.status == "achieved",
                    )
                    progress_result = await db.execute(progress_query)
                    achieved_count = progress_result.scalar() or 0

                    if achieved_count >= total_milestones:
                        # All milestones achieved! Issue certificate
                        now = utc_now()
                        verification_code = (
                            f"SB-{now.year}-{secrets.token_hex(4).upper()}"
                        )

                        enrollment.certificate_issued_at = now
                        enrollment.certificate_code = verification_code

                        logger.info(
                            f"Issuing certificate to {member.email} for {program.name}"
                        )

                        # Send email via centralized email service
                        try:
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="certificate",
                                to_email=member.email,
                                template_data={
                                    "member_name": member.first_name,
                                    "program_name": program.name,
                                    "completion_date": now.strftime("%B %d, %Y"),
                                    "verification_code": verification_code,
                                },
                            )
                            logger.info(f"Certificate email sent to {member.email}")
                        except Exception as email_err:
                            logger.error(
                                f"Failed to send certificate email to {member.email}: {email_err}"
                            )

            await db.commit()

        except Exception as e:
            logger.error(f"Error checking/issuing certificates: {e}")
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

    # Track weekly task execution
    last_weekly_run = None

    while True:
        try:
            await transition_cohort_statuses()
            await send_enrollment_reminders()
            await process_waitlist()
            await check_and_issue_certificates()

            # Weekly tasks (run on Sundays)
            now = utc_now()
            if now.weekday() == 6:  # Sunday
                if last_weekly_run is None or (now - last_weekly_run).days >= 1:
                    await send_weekly_progress_reports()
                    await check_attendance_and_notify()
                    last_weekly_run = now
                    logger.info(
                        "Completed weekly progress reports and attendance alerts"
                    )

        except Exception as e:
            logger.error(f"Error in periodic tasks: {e}")

        # Run every hour
        await asyncio.sleep(3600)


async def check_attendance_and_notify():
    """
    Check attendance patterns for all active cohorts and notify coaches.
    Sends weekly summary and individual alerts for at-risk students.
    """
    from datetime import timedelta

    from sqlalchemy import text

    async for db in get_async_db():
        try:
            now = utc_now()
            seven_days_ago = now - timedelta(days=7)
            period_str = (
                f"{seven_days_ago.strftime('%b %d')} - {now.strftime('%b %d, %Y')}"
            )

            # Get active cohorts with coaches
            cohort_query = (
                select(Cohort)
                .options(selectinload(Cohort.program))
                .where(Cohort.status == CohortStatus.ACTIVE)
            )
            cohort_result = await db.execute(cohort_query)
            cohorts = cohort_result.scalars().all()

            for cohort in cohorts:
                if not cohort.coach_id:
                    continue

                program = cohort.program

                # Get coach email
                coach_query = text(
                    "SELECT first_name, email FROM members WHERE id = :coach_id"
                )
                coach_result = await db.execute(
                    coach_query, {"coach_id": cohort.coach_id}
                )
                coach = coach_result.mappings().first()
                if not coach:
                    continue

                # Get sessions in last 7 days for this cohort
                session_query = text(
                    """
                    SELECT id FROM sessions 
                    WHERE cohort_id = :cohort_id 
                    AND starts_at >= :start_date 
                    AND starts_at <= :end_date
                    AND status = 'completed'
                """
                )
                session_result = await db.execute(
                    session_query,
                    {
                        "cohort_id": cohort.id,
                        "start_date": seven_days_ago,
                        "end_date": now,
                    },
                )
                session_ids = [row[0] for row in session_result.fetchall()]
                total_sessions = len(session_ids)

                if total_sessions == 0:
                    continue  # No sessions to report on

                # Get enrolled students
                enrollment_query = (
                    select(Enrollment, Member)
                    .join(Member, Enrollment.member_id == Member.id)
                    .where(
                        Enrollment.cohort_id == cohort.id,
                        Enrollment.status == EnrollmentStatus.ENROLLED,
                    )
                )
                enrollment_result = await db.execute(enrollment_query)
                enrollments = enrollment_result.all()

                student_stats = []
                at_risk_students = []

                for enrollment, member in enrollments:
                    # Get attendance for this student in these sessions
                    attendance_query = text(
                        """
                        SELECT status FROM attendance_records
                        WHERE member_id = :member_id
                        AND session_id = ANY(:session_ids)
                    """
                    )
                    attendance_result = await db.execute(
                        attendance_query,
                        {
                            "member_id": member.id,
                            "session_ids": session_ids,
                        },
                    )
                    records = attendance_result.fetchall()

                    present = sum(1 for r in records if r[0] in ("PRESENT", "LATE"))
                    absent = sum(1 for r in records if r[0] == "ABSENT")
                    late = sum(1 for r in records if r[0] == "LATE")
                    rate = (
                        round((present / total_sessions) * 100)
                        if total_sessions > 0
                        else 0
                    )

                    student_name = f"{member.first_name} {member.last_name}"
                    student_stats.append(
                        {
                            "name": student_name,
                            "present": present,
                            "absent": absent,
                            "late": late,
                            "rate": rate,
                        }
                    )

                    # Flag at-risk students
                    if rate < 70:
                        at_risk_students.append(
                            {
                                "name": student_name,
                                "issue": f"Low attendance ({rate}%)",
                            }
                        )

                        # Send individual alert via centralized email service
                        try:
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="low_attendance_alert",
                                to_email=coach["email"],
                                template_data={
                                    "coach_name": coach["first_name"],
                                    "student_name": student_name,
                                    "cohort_name": cohort.name,
                                    "issue": f"Attended only {present}/{total_sessions} sessions this week",
                                    "attendance_rate": rate,
                                    "suggestions": [
                                        "Schedule a check-in call with the student/parent",
                                        "Offer a makeup session if available",
                                        "Discuss any barriers to attendance",
                                    ],
                                },
                            )
                        except Exception as alert_err:
                            logger.error(
                                f"Failed to send alert for {student_name}: {alert_err}"
                            )

                # Send weekly summary to coach via centralized email service
                try:
                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="attendance_summary",
                        to_email=coach["email"],
                        template_data={
                            "coach_name": coach["first_name"],
                            "cohort_name": cohort.name,
                            "program_name": program.name if program else "Program",
                            "period": period_str,
                            "total_sessions": total_sessions,
                            "student_stats": student_stats,
                            "at_risk_students": at_risk_students,
                        },
                    )
                    logger.info(
                        f"Sent attendance summary to {coach['email']} for {cohort.name}"
                    )
                except Exception as summary_err:
                    logger.error(
                        f"Failed to send summary for {cohort.name}: {summary_err}"
                    )

        except Exception as e:
            logger.error(f"Error checking attendance: {e}")
        finally:
            await db.close()
            break


if __name__ == "__main__":
    # For manual testing or running as standalone process
    asyncio.run(check_attendance_and_notify())
