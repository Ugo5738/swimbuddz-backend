"""Background tasks for academy service automation."""

import asyncio
from datetime import timedelta

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    check_wallet_balance,
    debit_member_wallet,
    get_member_by_id,
    get_members_bulk,
    internal_get,
    internal_post,
)
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
)
from services.academy_service.services.installments import (
    build_schedule,
    mark_overdue_installments,
    sync_enrollment_installment_state,
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


async def evaluate_installment_compliance():
    """Mark overdue installments and enforce suspension/dropout rules.

    For each active enrollment:
    - Marks installments MISSED after the 24h grace window expires.
    - Suspends access if a required installment is unpaid.
    - At missed_count=2:
        - If cohort.admin_dropout_approval is True → DROPOUT_PENDING (admin reviews).
        - Otherwise → DROPPED automatically.
    - Sends admin notification when an enrollment moves to DROPOUT_PENDING.
    """
    async for db in get_async_db():
        try:
            now = utc_now()

            query = (
                select(Enrollment)
                .where(
                    Enrollment.cohort_id.is_not(None),
                    Enrollment.status.in_(
                        [
                            EnrollmentStatus.ENROLLED,
                            EnrollmentStatus.PENDING_APPROVAL,
                            EnrollmentStatus.DROPOUT_PENDING,
                            EnrollmentStatus.DROPPED,
                        ]
                    ),
                )
                .options(
                    selectinload(Enrollment.cohort).selectinload(Cohort.program),
                    selectinload(Enrollment.program),
                    selectinload(Enrollment.installments),
                )
            )
            result = await db.execute(query)
            enrollments = result.scalars().all()

            updated = 0
            newly_dropout_pending = []

            for enrollment in enrollments:
                cohort = enrollment.cohort
                program = enrollment.program or (cohort.program if cohort else None)
                if not cohort or not program:
                    continue

                installments = list(enrollment.installments or [])
                if not installments:
                    total_fee = int(
                        cohort.price_override
                        if cohort.price_override is not None
                        else (program.price_amount or 0)
                    )
                    if total_fee <= 0:
                        continue
                    try:
                        schedule = build_schedule(
                            total_fee=total_fee,
                            duration_weeks=int(program.duration_weeks),
                            cohort_start=cohort.start_date,
                        )
                    except ValueError:
                        continue

                    for item in schedule:
                        db.add(
                            EnrollmentInstallment(
                                enrollment_id=enrollment.id,
                                installment_number=item["installment_number"],
                                amount=item["amount"],
                                due_at=item["due_at"],
                            )
                        )
                    enrollment.price_snapshot_amount = total_fee
                    enrollment.currency_snapshot = program.currency or "NGN"
                    await db.flush()
                    refreshed = await db.execute(
                        select(EnrollmentInstallment)
                        .where(EnrollmentInstallment.enrollment_id == enrollment.id)
                        .order_by(EnrollmentInstallment.installment_number.asc())
                    )
                    installments = refreshed.scalars().all()

                mark_overdue_installments(installments, now=now)
                prev_status = enrollment.status
                prev_tuple = (
                    enrollment.status,
                    enrollment.payment_status,
                    enrollment.access_suspended,
                    enrollment.missed_installments_count,
                    enrollment.paid_installments_count,
                )
                sync_enrollment_installment_state(
                    enrollment=enrollment,
                    installments=installments,
                    duration_weeks=int(program.duration_weeks),
                    cohort_start=cohort.start_date,
                    cohort_requires_approval=bool(cohort.require_approval),
                    admin_dropout_approval=bool(cohort.admin_dropout_approval),
                    now=now,
                )
                next_tuple = (
                    enrollment.status,
                    enrollment.payment_status,
                    enrollment.access_suspended,
                    enrollment.missed_installments_count,
                    enrollment.paid_installments_count,
                )
                if prev_tuple != next_tuple:
                    updated += 1

                # Collect newly transitioned DROPOUT_PENDING enrollments for admin notification
                if (
                    prev_status != EnrollmentStatus.DROPOUT_PENDING
                    and enrollment.status == EnrollmentStatus.DROPOUT_PENDING
                ):
                    newly_dropout_pending.append((enrollment, cohort, program))

            await db.commit()

            # Send admin notifications for new dropout-pending cases
            for enrollment, cohort, program in newly_dropout_pending:
                try:
                    member = await get_member_by_id(
                        str(enrollment.member_id), calling_service="academy"
                    )
                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="admin_dropout_pending",
                        to_email="admin@swimbuddz.com",  # Replace with config-driven admin email
                        template_data={
                            "member_name": (
                                f"{member['first_name']} {member['last_name']}"
                                if member
                                else "Unknown member"
                            ),
                            "member_id": str(enrollment.member_id),
                            "enrollment_id": str(enrollment.id),
                            "program_name": program.name,
                            "cohort_name": cohort.name,
                            "missed_count": enrollment.missed_installments_count,
                        },
                    )
                    logger.info(
                        f"Sent dropout-pending admin notification for enrollment {enrollment.id}"
                    )
                except Exception as notify_err:
                    logger.error(
                        f"Failed to send dropout-pending notification for enrollment {enrollment.id}: {notify_err}"
                    )

            if updated:
                logger.info(
                    "Installment compliance updated %d enrollments",
                    updated,
                )
        except Exception as e:
            logger.error(f"Error evaluating installment compliance: {e}")
            await db.rollback()
        finally:
            await db.close()
            break


async def send_installment_payment_reminders():
    """Send payment reminders for upcoming installment due dates.

    Reminders are sent at 7, 3, and 1 day(s) before the due date.
    Students can pay early as soon as the first reminder arrives.
    Reminder keys stored on the installment to prevent duplicate sends.
    """
    async for db in get_async_db():
        try:
            now = utc_now()

            # Find PENDING installments due within the next 8 days
            query = (
                select(EnrollmentInstallment)
                .where(
                    EnrollmentInstallment.status == InstallmentStatus.PENDING,
                    EnrollmentInstallment.due_at > now,
                    EnrollmentInstallment.due_at <= now + timedelta(days=8),
                )
                .options(
                    selectinload(EnrollmentInstallment.enrollment)
                    .selectinload(Enrollment.cohort)
                    .selectinload(Cohort.program),
                    selectinload(EnrollmentInstallment.enrollment).selectinload(
                        Enrollment.program
                    ),
                )
            )
            result = await db.execute(query)
            installments = result.scalars().all()

            for installment in installments:
                enrollment = installment.enrollment
                if not enrollment:
                    continue

                # Skip suspended/dropped enrollments that are not in active state
                if enrollment.status in (
                    EnrollmentStatus.DROPPED,
                    EnrollmentStatus.WAITLIST,
                ):
                    continue

                cohort = enrollment.cohort
                program = enrollment.program or (cohort.program if cohort else None)
                if not cohort or not program:
                    continue

                days_until = (installment.due_at.date() - now.date()).days
                if days_until not in [7, 3, 1]:
                    continue

                reminder_key = (
                    f"installment_{installment.installment_number}_{days_until}d"
                )
                reminders_sent = enrollment.reminders_sent or []
                if reminder_key in reminders_sent:
                    continue

                member = await get_member_by_id(
                    str(enrollment.member_id), calling_service="academy"
                )
                if not member:
                    continue

                email_client = get_email_client()
                success = await email_client.send_template(
                    template_type="installment_payment_reminder",
                    to_email=member["email"],
                    template_data={
                        "member_name": member["first_name"],
                        "program_name": program.name,
                        "cohort_name": cohort.name,
                        "installment_number": installment.installment_number,
                        "total_installments": enrollment.total_installments,
                        "amount": installment.amount,
                        "currency": enrollment.currency_snapshot or "NGN",
                        "due_date": installment.due_at.strftime("%A, %B %d, %Y"),
                        "days_until": days_until,
                    },
                )

                if success:
                    new_reminders = reminders_sent + [reminder_key]
                    enrollment.reminders_sent = new_reminders
                    logger.info(
                        f"Sent {days_until}-day installment reminder to {member['email']} "
                        f"for installment {installment.installment_number} of enrollment {enrollment.id}"
                    )
                else:
                    logger.error(
                        f"Failed to send installment reminder to {member['email']}"
                    )

            await db.commit()

        except Exception as e:
            logger.error(f"Error sending installment payment reminders: {e}")
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


async def attempt_wallet_auto_deduction():
    """Attempt automatic wallet deduction for installments due today.

    On the installment due date (Monday 00:00 WAT), this task:
    1. Checks if the member's wallet has sufficient Bubbles for the installment.
    2. If yes → debits the wallet atomically and calls academy mark-paid.
    3. If no  → generates a Paystack checkout link and emails it to the student,
               so they can pay manually before the 24h grace window closes.

    Idempotency key ``wallet-installment-{enrollment_id}-{installment_number}``
    ensures the wallet debit is safe to retry even if the task runs multiple times
    within the same hour window.
    """
    settings_obj = get_settings()

    async for db in get_async_db():
        try:
            now = utc_now()
            # Window: installments whose due_at is within the past hour (±60 min).
            # This catches installments that just became due without requiring exact
            # second-level precision from the scheduler.
            window_start = now - timedelta(hours=1)
            window_end = now  # Only process past-due-or-just-due, not future

            query = (
                select(EnrollmentInstallment)
                .where(
                    EnrollmentInstallment.status == InstallmentStatus.PENDING,
                    EnrollmentInstallment.due_at >= window_start,
                    EnrollmentInstallment.due_at <= window_end,
                )
                .options(
                    selectinload(EnrollmentInstallment.enrollment)
                    .selectinload(Enrollment.cohort)
                    .selectinload(Cohort.program),
                    selectinload(EnrollmentInstallment.enrollment).selectinload(
                        Enrollment.program
                    ),
                )
            )
            result = await db.execute(query)
            due_installments = result.scalars().all()

            for installment in due_installments:
                enrollment = installment.enrollment
                if not enrollment:
                    continue

                # Skip if enrollment is no longer active
                if enrollment.status in (
                    EnrollmentStatus.DROPPED,
                    EnrollmentStatus.WAITLIST,
                    EnrollmentStatus.GRADUATED,
                ):
                    continue

                cohort = enrollment.cohort
                program = enrollment.program or (cohort.program if cohort else None)
                if not cohort or not program:
                    continue

                # Skip if wallet deduction was already attempted for this installment
                deduction_key = f"wallet_deduction_{installment.installment_number}"
                reminders_sent = enrollment.reminders_sent or []
                if deduction_key in reminders_sent:
                    continue

                member_auth_id = enrollment.member_auth_id
                idempotency_key = f"wallet-installment-{enrollment.id}-{installment.installment_number}"
                program_name = program.name if program else "Academy Program"
                cohort_name = cohort.name

                member = await get_member_by_id(
                    str(enrollment.member_id), calling_service="academy"
                )
                member_email = member.get("email") if member else None
                member_name = (
                    member.get("first_name", "Student") if member else "Student"
                )

                # Convert installment amount from kobo to Bubbles.
                # 1 Bubble = ₦100 = 10,000 kobo  (100 NGN/Bubble × 100 kobo/NGN).
                bubbles_needed = installment.amount // 10000

                # --- Attempt wallet deduction ---
                wallet_debited = False
                try:
                    balance_check = await check_wallet_balance(
                        member_auth_id,
                        required_amount=bubbles_needed,
                        calling_service="academy",
                    )
                    if balance_check and balance_check.get("sufficient"):
                        # Debit the wallet
                        await debit_member_wallet(
                            member_auth_id,
                            amount=bubbles_needed,
                            idempotency_key=idempotency_key,
                            description=(
                                f"Academy installment {installment.installment_number} "
                                f"for {program_name} – {cohort_name}"
                            ),
                            calling_service="academy",
                            transaction_type="purchase",
                            reference_type="enrollment_installment",
                            reference_id=str(installment.id),
                        )
                        wallet_debited = True
                        logger.info(
                            "Wallet auto-deduction successful for installment %s "
                            "(enrollment %s, %d Bubbles / %d kobo)",
                            installment.id,
                            enrollment.id,
                            bubbles_needed,
                            installment.amount,
                        )
                except Exception as wallet_err:
                    logger.warning(
                        "Wallet deduction failed for installment %s: %s",
                        installment.id,
                        wallet_err,
                    )

                if wallet_debited:
                    # Mark installment as paid via academy mark-paid endpoint
                    try:
                        mark_resp = await internal_post(
                            service_url=settings_obj.ACADEMY_SERVICE_URL,
                            path=f"/academy/admin/enrollments/{enrollment.id}/mark-paid",
                            calling_service="academy",
                            json={
                                "installment_id": str(installment.id),
                                "installment_number": installment.installment_number,
                                "payment_reference": idempotency_key,
                                "paid_at": now.isoformat(),
                            },
                        )
                        if mark_resp.status_code >= 400:
                            logger.error(
                                "Failed to mark installment %s as paid after wallet deduction: %s",
                                installment.id,
                                mark_resp.text,
                            )
                    except Exception as mark_err:
                        logger.error(
                            "mark-paid call failed for installment %s: %s",
                            installment.id,
                            mark_err,
                        )

                    # Send wallet payment confirmation email
                    if member_email:
                        try:
                            # installment.amount is in kobo; template expects NGN.
                            amount_ngn = installment.amount / 100
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="installment_payment_confirmation",
                                to_email=member_email,
                                template_data={
                                    "member_name": member_name,
                                    "installment_number": installment.installment_number,
                                    "total_installments": enrollment.total_installments,
                                    "amount": amount_ngn,
                                    "currency": enrollment.currency_snapshot or "NGN",
                                    "payment_reference": idempotency_key,
                                    "paid_at": now.strftime("%B %d, %Y"),
                                    "payment_method": "wallet",
                                },
                            )
                        except Exception as email_err:
                            logger.error(
                                "Failed to send wallet deduction confirmation to %s: %s",
                                member_email,
                                email_err,
                            )
                else:
                    # Insufficient wallet balance → generate Paystack checkout link
                    # and send to student so they can pay manually within the grace window.
                    if member_email:
                        try:
                            import secrets as _secrets

                            payment_ref = f"PAY-{_secrets.token_hex(3).upper()}"
                            init_resp = await internal_post(
                                service_url=settings_obj.PAYMENTS_SERVICE_URL,
                                path="/payments/internal/initialize",
                                calling_service="academy",
                                json={
                                    "reference": payment_ref,
                                    "member_auth_id": member_auth_id,
                                    "amount": installment.amount / 100,  # kobo → NGN
                                    "currency": enrollment.currency_snapshot or "NGN",
                                    "purpose": "academy_cohort",
                                    "callback_url": (
                                        f"/account/academy/enrollment-success"
                                        f"?enrollment_id={enrollment.id}"
                                    ),
                                    "metadata": {
                                        "payer_email": member_email,
                                        "enrollment_id": str(enrollment.id),
                                        "cohort_id": str(cohort.id),
                                        "installment_id": str(installment.id),
                                        "installment_number": installment.installment_number,
                                        "total_installments": enrollment.total_installments,
                                    },
                                },
                            )

                            checkout_url = None
                            if init_resp.status_code < 400:
                                init_data = init_resp.json()
                                checkout_url = init_data.get("authorization_url")

                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="installment_payment_reminder",
                                to_email=member_email,
                                template_data={
                                    "member_name": member_name,
                                    "program_name": program_name,
                                    "cohort_name": cohort_name,
                                    "installment_number": installment.installment_number,
                                    "total_installments": enrollment.total_installments,
                                    "amount": installment.amount,
                                    "currency": enrollment.currency_snapshot or "NGN",
                                    "due_date": installment.due_at.strftime(
                                        "%A, %B %d, %Y"
                                    ),
                                    "days_until": 0,  # Due today — urgent prompt
                                    "checkout_url": checkout_url,
                                    "insufficient_wallet": True,
                                },
                            )
                            logger.info(
                                "Sent Paystack fallback link to %s for installment %s",
                                member_email,
                                installment.id,
                            )
                        except Exception as paystack_err:
                            logger.error(
                                "Failed to generate Paystack fallback for installment %s: %s",
                                installment.id,
                                paystack_err,
                            )

                # Record that wallet deduction was attempted (prevents re-processing)
                enrollment.reminders_sent = reminders_sent + [deduction_key]

            await db.commit()

        except Exception as e:
            logger.error("Error in wallet auto-deduction task: %s", e)
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


async def check_attendance_and_notify():
    """
    Check attendance patterns for all active cohorts and notify coaches.
    Sends weekly summary and individual alerts for at-risk students.
    """
    from datetime import timedelta

    from libs.common.config import get_settings

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
                        1 for r in records if r.get("status") in ("PRESENT", "LATE")
                    )
                    absent = sum(1 for r in records if r.get("status") == "ABSENT")
                    late = sum(1 for r in records if r.get("status") == "LATE")
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


if __name__ == "__main__":
    # For manual testing or running as standalone process
    asyncio.run(check_attendance_and_notify())
