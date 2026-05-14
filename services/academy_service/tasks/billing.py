"""Billing-related background tasks: installment compliance, reminders, and auto-deduction."""

import secrets
from datetime import timedelta

from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    get_member_by_id,
    internal_post,
)
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
)
from services.academy_service.services.installments import (
    mark_overdue_installments,
    sync_enrollment_installment_state,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

logger = get_logger(__name__)


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
                    # Do not auto-create installment schedules in periodic jobs.
                    # Installment plans must come only from explicit member opt-in at checkout.
                    continue

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

                # Cohort fees are real-money only (founder policy May 2026) —
                # the reminder no longer mentions Bubbles or top-up flows.
                settings = get_settings()
                enrollment_url = (
                    f"{settings.FRONTEND_URL}/account/academy/enrollments/"
                    f"{enrollment.id}"
                )

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
                        "has_sufficient_balance": False,
                        "enrollment_url": enrollment_url,
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


async def attempt_wallet_auto_deduction():
    """Send a Paystack checkout link for installments due today.

    Per founder policy (May 2026), academy cohort fees must be paid in real
    money — Bubbles can no longer be used to pay installments. This task used
    to attempt a wallet debit first and only fall back to Paystack if the
    balance was insufficient; now it always generates and emails a Paystack
    checkout link.

    Function name retained for cron compatibility — see the
    "installment_payment_reminder" email template used below.
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

                # Idempotency: don't re-send a payment-due reminder twice for the
                # same installment within the same window. Key name kept stable
                # for backwards compatibility with already-emitted records.
                reminder_key = f"wallet_deduction_{installment.installment_number}"
                reminders_sent = enrollment.reminders_sent or []
                if reminder_key in reminders_sent:
                    continue

                member_auth_id = enrollment.member_auth_id
                program_name = program.name if program else "Academy Program"
                cohort_name = cohort.name

                member = await get_member_by_id(
                    str(enrollment.member_id), calling_service="academy"
                )
                member_email = member.get("email") if member else None
                member_name = (
                    member.get("first_name", "Student") if member else "Student"
                )

                # Per founder policy (May 2026): generate a Paystack checkout
                # link and email it to the student. Wallet deduction is no
                # longer attempted — cohort fees are real-money only.
                if member_email:
                    try:
                        payment_ref = f"PAY-{secrets.token_hex(3).upper()}"
                        init_resp = await internal_post(
                            service_url=settings_obj.PAYMENTS_SERVICE_URL,
                            path="/internal/payments/initialize",
                            calling_service="academy",
                            json={
                                "reference": payment_ref,
                                "member_auth_id": member_auth_id,
                                "amount": float(installment.amount) / KOBO_PER_NAIRA,
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
                                "insufficient_wallet": False,
                            },
                        )
                        logger.info(
                            "Sent Paystack payment link to %s for installment %s",
                            member_email,
                            installment.id,
                        )
                    except Exception as paystack_err:
                        logger.error(
                            "Failed to generate Paystack link for installment %s: %s",
                            installment.id,
                            paystack_err,
                        )

                # Record that the reminder was sent (prevents re-processing)
                enrollment.reminders_sent = reminders_sent + [reminder_key]

            await db.commit()

        except Exception as e:
            logger.error("Error in wallet auto-deduction task: %s", e)
            await db.rollback()
        finally:
            await db.close()
            break
