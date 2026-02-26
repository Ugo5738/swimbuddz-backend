"""Reporting-related background tasks: progress reports, certificates, and attendance."""

import asyncio
import secrets
from datetime import timedelta

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_id, get_members_bulk, internal_get
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentStatus,
    Milestone,
    StudentProgress,
)
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)


async def send_weekly_progress_reports():
    """
    Send weekly progress report emails to all enrolled students in active cohorts.
    Includes PDF attachment with full progress details.
    Runs once per week (controlled by caller).
    """
    from libs.common.pdf import generate_progress_report_pdf

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
                enrollment_query = select(Enrollment).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                )
                result = await db.execute(enrollment_query)
                enrollment_list = result.scalars().all()

                # Bulk-lookup member details
                pr_member_ids = list({str(e.member_id) for e in enrollment_list})
                pr_members = await get_members_bulk(
                    pr_member_ids, calling_service="academy"
                )
                pr_members_map = {m["id"]: m for m in pr_members}

                # Get total milestones for program
                milestone_query = select(Milestone).where(
                    Milestone.program_id == program.id
                )
                milestone_result = await db.execute(milestone_query)
                all_milestones = milestone_result.scalars().all()
                total_milestones = len(all_milestones)
                milestone_map = {m.id: m.name for m in all_milestones}

                for enrollment in enrollment_list:
                    member = pr_members_map.get(str(enrollment.member_id), {})
                    if not member:
                        continue
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
                            student_name=f"{member['first_name']} {member['last_name']}",
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
                            f"Failed to generate PDF for {member['email']}: {pdf_err}"
                        )

                    # Send email via centralized email service
                    try:
                        email_client = get_email_client()
                        await email_client.send_template(
                            template_type="progress_report",
                            to_email=member["email"],
                            template_data={
                                "member_name": member["first_name"],
                                "program_name": program.name,
                                "cohort_name": cohort.name,
                                "milestones_completed": completed_count,
                                "total_milestones": total_milestones,
                                "recent_achievements": recent,
                                "coach_feedback": feedback,
                                # Note: PDF attachment not yet supported via API
                            },
                        )
                        logger.info(f"Sent progress report to {member['email']}")
                    except Exception as email_err:
                        logger.error(
                            f"Failed to send progress report to {member['email']}: {email_err}"
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
                enrollment_query = select(Enrollment).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                    Enrollment.certificate_issued_at.is_(None),
                )
                enrollment_result = await db.execute(enrollment_query)
                cert_enrollments = enrollment_result.scalars().all()

                # Bulk-lookup member details
                cert_member_ids = list({str(e.member_id) for e in cert_enrollments})
                cert_members = await get_members_bulk(
                    cert_member_ids, calling_service="academy"
                )
                cert_members_map = {m["id"]: m for m in cert_members}

                for enrollment in cert_enrollments:
                    member = cert_members_map.get(str(enrollment.member_id), {})
                    if not member:
                        continue
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
                            f"Issuing certificate to {member['email']} for {program.name}"
                        )

                        # Send email via centralized email service
                        try:
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="certificate",
                                to_email=member["email"],
                                template_data={
                                    "member_name": member["first_name"],
                                    "program_name": program.name,
                                    "completion_date": now.strftime("%B %d, %Y"),
                                    "verification_code": verification_code,
                                },
                            )
                            logger.info(f"Certificate email sent to {member['email']}")
                        except Exception as email_err:
                            logger.error(
                                f"Failed to send certificate email to {member['email']}: {email_err}"
                            )

            await db.commit()

        except Exception as e:
            logger.error(f"Error checking/issuing certificates: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def check_attendance_and_notify():
    """
    Check attendance patterns for all active cohorts and notify coaches.
    Sends weekly summary and individual alerts for at-risk students.
    """
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

            settings = get_settings()

            for cohort in cohorts:
                if not cohort.coach_id:
                    continue

                program = cohort.program

                # Get coach info via members-service
                coach = await get_member_by_id(
                    str(cohort.coach_id), calling_service="academy"
                )
                if not coach:
                    continue

                # Get completed sessions in last 7 days via sessions-service
                sessions_resp = await internal_get(
                    service_url=settings.SESSIONS_SERVICE_URL,
                    path=f"/internal/cohorts/{cohort.id}/completed-session-ids",
                    calling_service="academy",
                    params={
                        "start_date": seven_days_ago.isoformat(),
                        "end_date": now.isoformat(),
                    },
                )
                if sessions_resp.status_code != 200:
                    continue
                session_ids = sessions_resp.json()
                total_sessions = len(session_ids)

                if total_sessions == 0:
                    continue  # No sessions to report on

                # Get enrolled students
                enrollment_query = select(Enrollment).where(
                    Enrollment.cohort_id == cohort.id,
                    Enrollment.status == EnrollmentStatus.ENROLLED,
                )
                enrollment_result = await db.execute(enrollment_query)
                att_enrollments = enrollment_result.scalars().all()

                # Bulk-lookup member details
                att_member_ids = list({str(e.member_id) for e in att_enrollments})
                att_members = await get_members_bulk(
                    att_member_ids, calling_service="academy"
                )
                att_members_map = {m["id"]: m for m in att_members}

                student_stats = []
                at_risk_students = []

                for enrollment in att_enrollments:
                    member = att_members_map.get(str(enrollment.member_id), {})
                    if not member:
                        continue

                    # Get attendance for this student via attendance-service
                    att_resp = await internal_get(
                        service_url=settings.ATTENDANCE_SERVICE_URL,
                        path=f"/internal/attendance/member/{enrollment.member_id}",
                        calling_service="academy",
                        params={"session_ids": ",".join(str(s) for s in session_ids)},
                    )
                    if att_resp.status_code == 200:
                        records = att_resp.json()
                    else:
                        records = []

                    present = sum(
                        1 for r in records if r.get("status") in ("present", "late")
                    )
                    absent = sum(1 for r in records if r.get("status") == "absent")
                    late = sum(1 for r in records if r.get("status") == "late")
                    rate = (
                        round((present / total_sessions) * 100)
                        if total_sessions > 0
                        else 0
                    )

                    student_name = f"{member['first_name']} {member['last_name']}"
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


async def run_periodic_tasks():
    """
    Run all periodic tasks in a loop.
    This can be started as a background process or via a task scheduler.
    """
    from services.academy_service.tasks.billing import (
        attempt_wallet_auto_deduction,
        evaluate_installment_compliance,
        send_installment_payment_reminders,
    )
    from services.academy_service.tasks.enrollment import (
        process_waitlist,
        send_enrollment_reminders,
        transition_cohort_statuses,
    )

    logger.info("Starting academy service periodic tasks...")

    # Track weekly task execution
    last_weekly_run = None

    while True:
        try:
            await transition_cohort_statuses()
            await send_enrollment_reminders()
            await attempt_wallet_auto_deduction()
            await send_installment_payment_reminders()
            await evaluate_installment_compliance()
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
