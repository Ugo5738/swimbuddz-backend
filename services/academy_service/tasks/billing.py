"""Billing-related background tasks: installment compliance, reminders, and auto-deduction."""

import secrets
from datetime import timedelta

from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_BUBBLE, KOBO_PER_NAIRA, kobo_to_bubbles
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    check_wallet_balance,
    debit_member_wallet,
    get_member_by_id,
    get_wallet_balance,
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

                # Fetch wallet balance to calculate shortfall for one-tap top-up link
                amount_bubbles = kobo_to_bubbles(installment.amount)
                wallet_balance_bubbles = 0
                shortfall_bubbles = amount_bubbles
                try:
                    wallet = await get_wallet_balance(
                        member["auth_id"], calling_service="academy"
                    )
                    if wallet:
                        wallet_balance_bubbles = wallet.get("balance", 0)
                        shortfall_bubbles = max(
                            0, amount_bubbles - wallet_balance_bubbles
                        )
                except Exception as wallet_err:
                    logger.warning(
                        f"Could not fetch wallet balance for member {enrollment.member_id}: {wallet_err}"
                    )

                settings = get_settings()
                enrollment_url = f"{settings.FRONTEND_URL}/account/academy/enrollments/{enrollment.id}"
                topup_url = (
                    f"{settings.FRONTEND_URL}/account/wallet/topup"
                    f"?prefill={shortfall_bubbles}&return_to=/account/academy/enrollments/{enrollment.id}"
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
                        "amount_bubbles": amount_bubbles,
                        "currency": enrollment.currency_snapshot or "NGN",
                        "due_date": installment.due_at.strftime("%A, %B %d, %Y"),
                        "days_until": days_until,
                        "wallet_balance_bubbles": wallet_balance_bubbles,
                        "shortfall_bubbles": shortfall_bubbles,
                        "has_sufficient_balance": shortfall_bubbles == 0,
                        "topup_url": topup_url,
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

                # Convert installment amount (kobo) to Bubbles.
                # 1 Bubble = ₦100 = 10,000 kobo; round up to ensure full coverage.
                bubbles_needed = (
                    installment.amount + KOBO_PER_BUBBLE - 1
                ) // KOBO_PER_BUBBLE

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
                            "(enrollment %s, %d Bubbles / ₦%.2f)",
                            installment.id,
                            enrollment.id,
                            bubbles_needed,
                            installment.amount / KOBO_PER_NAIRA,
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
                            email_client = get_email_client()
                            await email_client.send_template(
                                template_type="installment_payment_confirmation",
                                to_email=member_email,
                                template_data={
                                    "member_name": member_name,
                                    "installment_number": installment.installment_number,
                                    "total_installments": enrollment.total_installments,
                                    "amount": float(installment.amount)
                                    / KOBO_PER_NAIRA,
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
                            payment_ref = f"PAY-{secrets.token_hex(3).upper()}"
                            init_resp = await internal_post(
                                service_url=settings_obj.PAYMENTS_SERVICE_URL,
                                path="/payments/internal/initialize",
                                calling_service="academy",
                                json={
                                    "reference": payment_ref,
                                    "member_auth_id": member_auth_id,
                                    "amount": float(installment.amount)
                                    / KOBO_PER_NAIRA,
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
