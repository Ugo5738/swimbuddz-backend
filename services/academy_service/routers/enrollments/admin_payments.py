"""Admin payment-related endpoints: mark-paid, dropout-action."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_id, internal_post
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
)
from services.academy_service.routers._shared import (
    _sync_installment_state_for_enrollment,
    sync_enrollment_installment_state,
)
from services.academy_service.schemas import (
    AdminDropoutActionRequest,
    EnrollmentMarkPaidRequest,
    EnrollmentResponse,
)
from services.academy_service.services.chat_sync import (
    ensure_cohort_channel,
    reconcile_cohort_membership,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

# Sentinel stored in Enrollment.reminders_sent to make the enrollment
# confirmation email idempotent. mark-paid is legitimately called more
# than once for the same enrollment — the Paystack webhook AND the
# client-side /paystack/verify fallback on the enrollment-success page
# both reach it (and React 18 StrictMode can double-fire the verify in
# dev). Without this guard each call re-sends the confirmation email.
_CONFIRMATION_SENT_KEY = "enrollment_confirmation"

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/admin/enrollments/{enrollment_id}/mark-paid", response_model=EnrollmentResponse
)
async def admin_mark_enrollment_paid(
    enrollment_id: uuid.UUID,
    payload: EnrollmentMarkPaidRequest | None = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Mark an enrollment as paid (service-to-service call from payments_service).
    Updates payment_status to PAID and enrollment status to ENROLLED if pending.
    """
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            # Eager-load coach_assignments on the cohort. The enrollment
            # confirmation email below reads `enrollment.cohort.coach_assignments`
            # to resolve the lead coach name; without this preload the access
            # triggers a lazy-load on the async session and raises
            # `greenlet_spawn has not been called` — which is then caught by
            # the try/except wrapping the email send and gets logged as a
            # silent warning. The confirmation email never reaches the member.
            # (Backref defined on CoachAssignment.cohort in
            # services/academy_service/models/progress.py.)
            selectinload(Enrollment.cohort).selectinload(Cohort.coach_assignments),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if payload and payload.paid_at:
        now_dt = payload.paid_at
    else:
        now_dt = utc_now()
    mark_payload = payload or EnrollmentMarkPaidRequest()

    installments = await _sync_installment_state_for_enrollment(
        db, enrollment, now_dt=now_dt
    )
    was_any_installment_paid = enrollment.paid_installments_count > 0

    if installments:
        paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
        target_installment: EnrollmentInstallment | None = None

        if mark_payload.clear_installments:
            for inst in installments:
                await db.delete(inst)
            enrollment.uses_installments = False
            enrollment.total_installments = 0
            enrollment.paid_installments_count = 0
            enrollment.missed_installments_count = 0
            enrollment.access_suspended = False
            enrollment.payment_status = PaymentStatus.PAID
            enrollment.paid_at = now_dt
            if mark_payload.payment_reference:
                enrollment.payment_reference = mark_payload.payment_reference
            if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
                cohort = enrollment.cohort
                if not cohort or not cohort.require_approval:
                    enrollment.status = EnrollmentStatus.ENROLLED
            installments = []
        elif mark_payload.installment_id:
            target_installment = next(
                (i for i in installments if i.id == mark_payload.installment_id), None
            )
            if not target_installment:
                raise HTTPException(status_code=400, detail="installment_id is invalid")
        elif mark_payload.installment_number:
            target_installment = next(
                (
                    i
                    for i in installments
                    if i.installment_number == mark_payload.installment_number
                ),
                None,
            )
            if not target_installment:
                raise HTTPException(
                    status_code=400, detail="installment_number is invalid"
                )
        else:
            target_installment = next(
                (i for i in installments if i.status not in paid_statuses),
                None,
            )

        if not mark_payload.clear_installments:
            # Member-initiated custom amount: if amount_kobo is provided AND
            # exceeds the target installment's amount, roll the overage
            # forward across subsequent installments. This is the "pay ahead"
            # / "recover missed auto-collection" flow (founder policy May 2026).
            if (
                target_installment
                and mark_payload.amount_kobo is not None
                and mark_payload.amount_kobo > target_installment.amount
            ):
                from services.academy_service.services.installments import (
                    apply_member_payment_across_installments,
                )

                # Apply across all PENDING installments starting from target.
                # The helper marks them PAID in order and reduces the trailing
                # one if the amount doesn't cleanly cover whole installments.
                payable = [
                    i
                    for i in installments
                    if i.installment_number >= target_installment.installment_number
                    and i.status not in paid_statuses
                ]
                apply_member_payment_across_installments(
                    amount_kobo=mark_payload.amount_kobo,
                    installments=payable,
                    now=now_dt,
                    payment_reference=mark_payload.payment_reference,
                )
            elif target_installment and target_installment.status not in paid_statuses:
                target_installment.status = InstallmentStatus.PAID
                target_installment.paid_at = now_dt
                target_installment.payment_reference = mark_payload.payment_reference

            if mark_payload.payment_reference:
                enrollment.payment_reference = mark_payload.payment_reference

            await _sync_installment_state_for_enrollment(db, enrollment, now_dt=now_dt)
    else:
        enrollment.payment_status = PaymentStatus.PAID
        enrollment.payment_reference = mark_payload.payment_reference
        enrollment.paid_at = now_dt
        if enrollment.status == EnrollmentStatus.PENDING_APPROVAL:
            cohort = enrollment.cohort
            if not cohort or not cohort.require_approval:
                enrollment.status = EnrollmentStatus.ENROLLED

    await db.commit()

    # Best-effort: reconcile chat-channel membership. Idempotent — safe to
    # call on every mark-paid hit (Paystack webhook + verify fallback +
    # subsequent installment payments). Closes the gap where a member who
    # self-enrolled (PENDING_APPROVAL) becomes ENROLLED only via payment
    # but no other code path tells chat about it. See
    # CHAT_SERVICE_DESIGN.md §4.2 for the derived-membership contract.
    if (
        enrollment.status == EnrollmentStatus.ENROLLED
        and enrollment.cohort_id is not None
    ):
        cohort = enrollment.cohort
        if cohort is not None:
            await ensure_cohort_channel(
                cohort_id=cohort.id,
                cohort_name=cohort.name,
                created_by_member_id=cohort.coach_id,
            )
        await reconcile_cohort_membership(
            cohort_id=enrollment.cohort_id,
            member_id=enrollment.member_id,
            enrollment_id=enrollment.id,
            action="add",
        )

    should_send_confirmation = (
        not was_any_installment_paid and enrollment.paid_installments_count > 0
    ) or (not installments and enrollment.payment_status == PaymentStatus.PAID)

    # Idempotency guard: mark-paid is reached by both the Paystack webhook
    # and the client-side verify fallback, so the trigger condition above
    # can be true on more than one call for the same enrollment. Only the
    # first send actually goes out; the sentinel is persisted in
    # reminders_sent (same JSON-list pattern the reminder tasks use).
    already_sent = _CONFIRMATION_SENT_KEY in (enrollment.reminders_sent or [])

    # Send enrollment confirmation email on first successful installment.
    if should_send_confirmation and not already_sent:
        try:
            member_data = await get_member_by_id(
                str(enrollment.member_id), calling_service="academy"
            )
            if member_data:
                member_email = member_data.get("email")
                member_name = member_data.get("first_name", "Member")

                if member_email and enrollment.cohort:
                    program_name = (
                        enrollment.program.name
                        if enrollment.program
                        else "Academy Program"
                    )
                    cohort_name = enrollment.cohort.name
                    start_date = (
                        enrollment.cohort.start_date.strftime("%B %d, %Y")
                        if enrollment.cohort.start_date
                        else "TBD"
                    )
                    location = enrollment.cohort.location_name or None

                    # Resolve coach name from assignments if available
                    coach_name = None
                    if (
                        hasattr(enrollment.cohort, "coach_assignments")
                        and enrollment.cohort.coach_assignments
                    ):
                        lead = (
                            next(
                                (
                                    a
                                    for a in enrollment.cohort.coach_assignments
                                    if getattr(a, "role", None) == "lead"
                                ),
                                None,
                            )
                            or enrollment.cohort.coach_assignments[0]
                        )
                        if lead and hasattr(lead, "coach") and lead.coach:
                            coach_name = f"{lead.coach.first_name} {lead.coach.last_name}".strip()

                    # Build installment schedule lines if paying in installments
                    is_installment = bool(installments)
                    installment_schedule = None
                    if is_installment and installments:
                        installment_schedule = [
                            f"Installment {inst.installment_number}: "
                            f"₦{round(inst.amount / 100):,} due "
                            f"{inst.due_at.strftime('%B %d, %Y') if hasattr(inst.due_at, 'strftime') else str(inst.due_at)}"
                            for inst in sorted(
                                installments, key=lambda i: i.installment_number
                            )
                        ]

                    email_client = get_email_client()
                    await email_client.send_template(
                        template_type="enrollment_confirmation",
                        to_email=member_email,
                        template_data={
                            "member_name": member_name,
                            "program_name": program_name,
                            "cohort_name": cohort_name,
                            "start_date": start_date,
                            "location": location,
                            "coach_name": coach_name,
                            "is_installment": is_installment,
                            "installment_schedule": installment_schedule,
                            # Powers the deep links in the email's
                            # "Before Your First Session" checklist
                            # (curriculum + prep → enrollment dashboard).
                            "enrollment_id": str(enrollment.id),
                        },
                    )

                    # Persist the sentinel only after a successful send so a
                    # transient email failure can still be retried by the
                    # next mark-paid call (webhook/verify).
                    enrollment.reminders_sent = list(
                        enrollment.reminders_sent or []
                    ) + [_CONFIRMATION_SENT_KEY]
                    flag_modified(enrollment, "reminders_sent")
                    await db.commit()
        except Exception as e:
            logger.warning(f"Failed to send enrollment confirmation email: {e}")

    # Activate the academy tier on the member for the duration of this cohort.
    # We do this on every installment payment (not just the first) so that if a
    # later cohort ends after the current academy_paid_until, the date is extended.
    # The members_service endpoint keeps whichever date is later, so it is safe to
    # call multiple times.
    try:
        _settings = get_settings()
        cohort_end = enrollment.cohort.end_date if enrollment.cohort else None
        member_auth_id = None
        if enrollment.member_id:
            member_data = await get_member_by_id(
                str(enrollment.member_id), calling_service="academy"
            )
            if member_data:
                member_auth_id = member_data.get("auth_id")

        if cohort_end and member_auth_id:
            end_iso = (
                cohort_end.isoformat()
                if hasattr(cohort_end, "isoformat")
                else str(cohort_end)
            )
            await internal_post(
                service_url=_settings.MEMBERS_SERVICE_URL,
                path=f"/admin/members/by-auth/{member_auth_id}/academy/activate",
                calling_service="academy",
                json={"cohort_end_date": end_iso},
            )
        else:
            logger.warning(
                f"Skipping academy tier activation for enrollment {enrollment_id}: "
                f"cohort_end={cohort_end}, member_auth_id={member_auth_id}"
            )
    except Exception as e:
        # Non-fatal — enrollment payment succeeded; log and continue
        logger.error(
            f"Failed to activate academy tier for enrollment {enrollment_id}: {e}"
        )

    # Re-fetch with relationships for response
    result = await db.execute(query)
    return result.scalar_one()


@router.post(
    "/admin/enrollments/{enrollment_id}/dropout-action",
    response_model=EnrollmentResponse,
)
async def admin_dropout_action(
    enrollment_id: uuid.UUID,
    payload: AdminDropoutActionRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin action on a DROPOUT_PENDING enrollment.

    action="approve" → confirms the dropout, moves enrollment to DROPPED.
    action="reverse"  → reinstates the student, moves enrollment back to ENROLLED
                        (or PENDING_APPROVAL if the cohort requires it).
                        The missed_installments_count is NOT reset — it is a
                        permanent behavioral counter.
    """
    query = (
        select(Enrollment)
        .where(Enrollment.id == enrollment_id)
        .options(
            selectinload(Enrollment.cohort).selectinload(Cohort.program),
            selectinload(Enrollment.program),
            selectinload(Enrollment.installments),
            selectinload(Enrollment.progress_records),
        )
    )
    result = await db.execute(query)
    enrollment = result.scalar_one_or_none()

    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.status != EnrollmentStatus.DROPOUT_PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Enrollment is not in dropout_pending state (current: {enrollment.status})",
        )

    if payload.action == "approve":
        enrollment.status = EnrollmentStatus.DROPPED
        enrollment.access_suspended = True
        enrollment.payment_status = PaymentStatus.FAILED
        # Preserve the original drop date if already stamped at the
        # DROPOUT_PENDING transition; otherwise stamp now.
        if enrollment.dropped_at is None:
            enrollment.dropped_at = utc_now()
        logger.info(
            f"Admin {current_user.user_id} approved dropout for enrollment {enrollment_id}"
        )

    elif payload.action == "reverse":
        # Reinstate the student. Access is restored only if installments are current.
        # missed_installments_count stays as-is (permanent behavioral record).
        cohort = enrollment.cohort
        requires_approval = bool(cohort.require_approval) if cohort else False
        enrollment.status = (
            EnrollmentStatus.PENDING_APPROVAL
            if requires_approval
            else EnrollmentStatus.ENROLLED
        )
        enrollment.access_suspended = False
        enrollment.payment_status = PaymentStatus.PAID

        # Re-sync to correctly set access_suspended based on actual installment state
        if cohort:
            program = enrollment.program or (cohort.program if cohort else None)
            if program:
                installments = list(enrollment.installments or [])
                sync_enrollment_installment_state(
                    enrollment=enrollment,
                    installments=installments,
                    duration_weeks=int(program.duration_weeks),
                    cohort_start=cohort.start_date,
                    cohort_requires_approval=requires_approval,
                    admin_dropout_approval=bool(cohort.admin_dropout_approval),
                    now=utc_now(),
                )
                # Override: admin has manually reinstated, so force out of dropout states
                if enrollment.status in (
                    EnrollmentStatus.DROPPED,
                    EnrollmentStatus.DROPOUT_PENDING,
                ):
                    enrollment.status = (
                        EnrollmentStatus.PENDING_APPROVAL
                        if requires_approval
                        else EnrollmentStatus.ENROLLED
                    )

        # Clear drop timestamp last so the sync above can't re-stamp it.
        # Coach payout calculator treats this enrollment as continuously active again.
        enrollment.dropped_at = None

        logger.info(
            f"Admin {current_user.user_id} reversed dropout for enrollment {enrollment_id}"
        )

    else:
        raise HTTPException(
            status_code=400,
            detail="Invalid action. Must be 'approve' or 'reverse'.",
        )

    await db.commit()

    # Reflect the dropout decision in chat membership. Best-effort.
    if enrollment.cohort_id is not None:
        chat_action = "remove" if payload.action == "approve" else "add"
        await reconcile_cohort_membership(
            cohort_id=enrollment.cohort_id,
            member_id=enrollment.member_id,
            enrollment_id=enrollment.id,
            action=chat_action,
        )

    result = await db.execute(query)
    return result.scalar_one()
