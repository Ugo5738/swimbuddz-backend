"""
Email API router for the Communications Service.

Centralizes email sending functionality so other services can send emails
by calling this API instead of importing email functions directly.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.emails.core import send_email
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from pydantic import BaseModel, EmailStr
from services.communications_service.models import MessageLog, MessageRecipientType
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

router = APIRouter(prefix="/email", tags=["email"])


# ===== SCHEMAS =====


class EmailRequest(BaseModel):
    """Request schema for sending a single email."""

    to_email: EmailStr
    subject: str
    body: str
    html_body: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None


class BulkEmailRequest(BaseModel):
    """Request schema for sending bulk emails."""

    to_emails: List[EmailStr]
    subject: str
    body: str
    html_body: Optional[str] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    sender_id: Optional[uuid.UUID] = None  # For logging purposes


class EmailResponse(BaseModel):
    """Response schema for email operations."""

    success: bool
    message: str
    sent_count: int = 0
    failed_count: int = 0


class TemplatedEmailRequest(BaseModel):
    """Request schema for templated emails (academy, payments, etc.)."""

    template_type: str  # e.g., "enrollment_confirmation", "payment_approved"
    to_email: EmailStr
    template_data: dict


# ===== ENDPOINTS =====


@router.post("/send", response_model=EmailResponse)
async def send_single_email(
    request: EmailRequest,
    current_user: AuthUser = Depends(require_service_role),
):
    """
    Send a single email.

    Requires service role authentication (internal service-to-service calls).
    """
    success = await send_email(
        to_email=request.to_email,
        subject=request.subject,
        body=request.body,
        html_body=request.html_body,
        from_email=request.from_email,
        from_name=request.from_name,
    )

    if success:
        return EmailResponse(
            success=True, message="Email sent successfully", sent_count=1
        )
    else:
        return EmailResponse(
            success=False, message="Failed to send email", failed_count=1
        )


@router.post("/send-bulk", response_model=EmailResponse)
async def send_bulk_emails(
    request: BulkEmailRequest,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Send bulk emails to multiple recipients.

    Requires service role authentication (internal service-to-service calls).
    Logs the operation in message_logs table.
    """
    sent_count = 0
    failed_count = 0

    for email in request.to_emails:
        success = await send_email(
            to_email=email,
            subject=request.subject,
            body=request.body,
            html_body=request.html_body,
            from_email=request.from_email,
            from_name=request.from_name,
        )
        if success:
            sent_count += 1
        else:
            failed_count += 1

    # Log the bulk operation if sender_id provided
    if request.sender_id:
        log = MessageLog(
            sender_id=request.sender_id,
            recipient_type=MessageRecipientType.COHORT,  # Using cohort as bulk type
            recipient_id=None,
            recipient_count=len(request.to_emails),
            subject=request.subject,
            body=request.body[:1000],  # Truncate for storage
            sent_at=datetime.now(timezone.utc),
        )
        db.add(log)
        await db.commit()

    all_success = failed_count == 0
    message = (
        f"Sent {sent_count}/{len(request.to_emails)} emails"
        if all_success
        else f"Sent {sent_count}, failed {failed_count} emails"
    )

    return EmailResponse(
        success=all_success,
        message=message,
        sent_count=sent_count,
        failed_count=failed_count,
    )


@router.post("/template", response_model=EmailResponse)
async def send_templated_email(
    request: TemplatedEmailRequest,
    current_user: AuthUser = Depends(require_service_role),
):
    """
    Send a templated email using predefined templates.

    Template types:
    - enrollment_confirmation: Academy enrollment confirmed
    - enrollment_reminder: Reminder before cohort starts
    - waitlist_promotion: Student moved off waitlist
    - progress_report: Student progress report
    - certificate: Course completion certificate
    - attendance_summary: Weekly attendance summary for coach
    - coach_assignment: Coach assigned to a cohort
    - low_attendance_alert: Alert about student low attendance
    - coach_agreement_signed: Coach agreement signing confirmation
    - coach_grade_change: Coach grade promotion/update notification
    - shadow_assignment: Shadow coach assignment notification
    - coach_readiness: Coach readiness assessment result
    - coach_application_approved: Coach application approved
    - coach_application_rejected: Coach application rejected
    - coach_application_more_info: More info requested from coach applicant
    - member_approved: Member application approved
    - member_rejected: Member application rejected
    - payment_approved: Payment was approved
    - session_confirmation: Session booking confirmed
    - store_order_confirmation: Store order confirmed
    - store_order_ready: Store order ready for pickup/shipped

    Requires service role authentication (internal service-to-service calls).
    """
    from services.communications_service.templates import (
        academy,
        coaching,
        members,
        payments,
        sessions,
        store,
    )

    template_handlers = {
        # --- Academy templates ---
        "enrollment_confirmation": lambda d: academy.send_enrollment_confirmation_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            program_name=d.get("program_name", ""),
            cohort_name=d.get("cohort_name", ""),
            start_date=d.get("start_date", ""),
        ),
        "enrollment_reminder": lambda d: academy.send_enrollment_reminder_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            program_name=d.get("program_name", ""),
            cohort_name=d.get("cohort_name", ""),
            start_date=d.get("start_date", ""),
            start_time=d.get("start_time", ""),
            location=d.get("location", ""),
            days_until=d.get("days_until", 7),
        ),
        "waitlist_promotion": lambda d: academy.send_waitlist_promotion_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            program_name=d.get("program_name", ""),
            cohort_name=d.get("cohort_name", ""),
        ),
        "progress_report": lambda d: academy.send_progress_report_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            program_name=d.get("program_name", ""),
            cohort_name=d.get("cohort_name", ""),
            milestones_completed=d.get("milestones_completed", 0),
            total_milestones=d.get("total_milestones", 0),
            recent_achievements=d.get("recent_achievements", []),
            coach_feedback=d.get("coach_feedback", []),
        ),
        "certificate": lambda d: academy.send_certificate_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            program_name=d.get("program_name", ""),
            completion_date=d.get("completion_date", ""),
            verification_code=d.get("verification_code", ""),
        ),
        "attendance_summary": lambda d: academy.send_attendance_summary_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            cohort_name=d.get("cohort_name", ""),
            program_name=d.get("program_name", ""),
            period=d.get("period", ""),
            total_sessions=d.get("total_sessions", 0),
            student_stats=d.get("student_stats", []),
            at_risk_students=d.get("at_risk_students", []),
        ),
        "coach_assignment": lambda d: academy.send_coach_assignment_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            program_name=d.get("program_name", ""),
            cohort_name=d.get("cohort_name", ""),
            start_date=d.get("start_date", ""),
        ),
        "low_attendance_alert": lambda d: academy.send_low_attendance_alert_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            student_name=d.get("student_name", ""),
            cohort_name=d.get("cohort_name", ""),
            issue=d.get("issue", ""),
            attendance_rate=d.get("attendance_rate", 0),
            suggestions=d.get("suggestions", []),
        ),
        # --- Coaching templates ---
        "coach_agreement_signed": lambda d: coaching.send_coach_agreement_signed_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            version=d.get("version", ""),
            signed_at=d.get("signed_at", ""),
        ),
        "coach_grade_change": lambda d: coaching.send_coach_grade_change_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            category=d.get("category", ""),
            old_grade=d.get("old_grade", ""),
            new_grade=d.get("new_grade", ""),
            effective_date=d.get("effective_date", ""),
        ),
        "shadow_assignment": lambda d: coaching.send_shadow_assignment_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            lead_coach_name=d.get("lead_coach_name", ""),
            cohort_name=d.get("cohort_name", ""),
            program_name=d.get("program_name", ""),
            start_date=d.get("start_date", ""),
            end_date=d.get("end_date", ""),
        ),
        "coach_readiness": lambda d: coaching.send_coach_readiness_email(
            to_email=request.to_email,
            coach_name=d.get("coach_name", ""),
            target_grade=d.get("target_grade", ""),
            is_ready=d.get("is_ready", False),
            passed_checks=d.get("passed_checks", []),
            pending_checks=d.get("pending_checks", []),
        ),
        "coach_application_approved": lambda d: (
            coaching.send_coach_application_approved_email(
                to_email=request.to_email,
                coach_name=d.get("coach_name", ""),
                onboarding_url=d.get(
                    "onboarding_url", "https://swimbuddz.com/coach/onboarding"
                ),
            )
        ),
        "coach_application_rejected": lambda d: (
            coaching.send_coach_application_rejected_email(
                to_email=request.to_email,
                coach_name=d.get("coach_name", ""),
                rejection_reason=d.get("rejection_reason", ""),
            )
        ),
        "coach_application_more_info": lambda d: (
            coaching.send_coach_application_more_info_email(
                to_email=request.to_email,
                coach_name=d.get("coach_name", ""),
                message=d.get("message", ""),
            )
        ),
        # --- Member templates ---
        "member_approved": lambda d: members.send_member_approved_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
        ),
        "member_rejected": lambda d: members.send_member_rejected_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            rejection_reason=d.get(
                "rejection_reason", "Does not meet current criteria"
            ),
        ),
        # --- Payment templates ---
        "payment_approved": lambda d: payments.send_payment_approved_email(
            to_email=request.to_email,
            payment_reference=d.get("payment_reference", ""),
            purpose=d.get("purpose", ""),
            amount=d.get("amount", 0),
            currency=d.get("currency", "NGN"),
        ),
        # --- Session templates ---
        "session_confirmation": lambda d: sessions.send_session_confirmation_email(
            to_email=request.to_email,
            member_name=d.get("member_name", ""),
            member_id=d.get("member_id", ""),
            session_title=d.get("session_title", ""),
            session_date=d.get("session_date", ""),
            session_time=d.get("session_time", ""),
            session_location=d.get("session_location", ""),
            session_address=d.get("session_address", ""),
            amount_paid=d.get("amount_paid", 0),
            ride_share_area=d.get("ride_share_area"),
            pickup_location=d.get("pickup_location"),
            pickup_description=d.get("pickup_description"),
            departure_time=d.get("departure_time"),
            ride_distance=d.get("ride_distance"),
            ride_duration=d.get("ride_duration"),
            currency=d.get("currency", "NGN"),
        ),
        # --- Store templates ---
        "store_order_confirmation": lambda d: store.send_store_order_confirmation_email(
            to_email=request.to_email,
            customer_name=d.get("customer_name", ""),
            order_number=d.get("order_number", ""),
            items=d.get("items", []),
            subtotal=d.get("subtotal", 0),
            discount=d.get("discount", 0),
            delivery_fee=d.get("delivery_fee", 0),
            total=d.get("total", 0),
            fulfillment_type=d.get("fulfillment_type", "pickup"),
            pickup_location=d.get("pickup_location"),
            delivery_address=d.get("delivery_address"),
        ),
        "store_order_ready": lambda d: store.send_store_order_ready_email(
            to_email=request.to_email,
            customer_name=d.get("customer_name", ""),
            order_number=d.get("order_number", ""),
            fulfillment_type=d.get("fulfillment_type", "pickup"),
            pickup_location=d.get("pickup_location"),
            tracking_number=d.get("tracking_number"),
        ),
    }

    handler = template_handlers.get(request.template_type)
    if not handler:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown template type: {request.template_type}. "
            f"Available: {list(template_handlers.keys())}",
        )

    try:
        success = await handler(request.template_data)
        if success:
            return EmailResponse(
                success=True,
                message=f"Templated email '{request.template_type}' sent successfully",
                sent_count=1,
            )
        else:
            return EmailResponse(
                success=False,
                message=f"Failed to send templated email '{request.template_type}'",
                failed_count=1,
            )
    except Exception as e:
        logger.error(f"Error sending templated email: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error sending templated email: {str(e)}",
        )
