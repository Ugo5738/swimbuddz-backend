"""
Messaging router for Communications Service.

Provides endpoints for coaches and admins to send messages to cohorts and individual students.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import is_admin_or_service, require_coach
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import (
    get_member_by_auth_id,
    get_member_by_id,
    get_members_bulk,
    internal_get,
)
from libs.db.session import get_async_db
from services.communications_service.models import MessageLog, MessageRecipientType
from services.communications_service.schemas import (
    CohortMessageCreate,
    MessageLogResponse,
    MessageResponse,
    StudentMessageCreate,
)
from services.communications_service.templates.messaging import send_message_email
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/messages", tags=["messaging"])


async def get_member_id_from_auth(auth_id: str) -> uuid.UUID:
    """Get member_id from auth_id via members-service."""
    member = await get_member_by_auth_id(auth_id, calling_service="communications")
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found",
        )
    return uuid.UUID(member["id"])


async def validate_coach_owns_cohort(
    coach_member_id: uuid.UUID, cohort_id: uuid.UUID
) -> dict:
    """
    Validate that the coach is assigned to the cohort via academy-service.
    Returns cohort info if valid.
    """
    settings = get_settings()
    resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/cohorts/{cohort_id}",
        calling_service="communications",
    )
    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cohort not found",
        )
    resp.raise_for_status()
    cohort = resp.json()

    if str(cohort.get("coach_id")) != str(coach_member_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the assigned coach for this cohort",
        )

    return cohort


async def get_cohort_enrolled_students(
    cohort_id: uuid.UUID,
) -> List[dict]:
    """Get all enrolled students in a cohort with their email addresses.

    Queries academy-service for enrollments, then members-service for member details.
    """
    settings = get_settings()
    # Get enrollments from academy-service
    enroll_resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/cohorts/{cohort_id}/enrolled-students",
        calling_service="communications",
    )
    enroll_resp.raise_for_status()
    enrollments = enroll_resp.json()  # [{enrollment_id, member_id, status}, ...]

    if not enrollments:
        return []

    # Get member details from members-service
    member_ids = [str(e["member_id"]) for e in enrollments]
    members = await get_members_bulk(member_ids, calling_service="communications")
    member_map = {m["id"]: m for m in members}

    results = []
    for e in enrollments:
        m = member_map.get(str(e["member_id"]), {})
        results.append(
            {
                "enrollment_id": e["enrollment_id"],
                "member_id": e["member_id"],
                "email": m.get("email"),
                "first_name": m.get("first_name"),
                "last_name": m.get("last_name"),
            }
        )
    return results


async def get_enrollment_student(enrollment_id: uuid.UUID) -> dict:
    """Get student info from enrollment via academy-service and members-service."""
    settings = get_settings()
    enroll_resp = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/academy/internal/enrollments/{enrollment_id}",
        calling_service="communications",
    )
    if enroll_resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enrollment not found",
        )
    enroll_resp.raise_for_status()
    enrollment = enroll_resp.json()

    # Get member details
    member = (
        await get_member_by_auth_id(
            str(enrollment.get("member_auth_id", "")), calling_service="communications"
        )
        if enrollment.get("member_auth_id")
        else None
    )

    if not member and enrollment.get("member_id"):
        from libs.common.service_client import get_member_by_id

        member = await get_member_by_id(
            str(enrollment["member_id"]), calling_service="communications"
        )

    return {
        "enrollment_id": str(enrollment.get("id", enrollment_id)),
        "member_id": enrollment.get("member_id"),
        "cohort_id": enrollment.get("cohort_id"),
        "email": member.get("email") if member else None,
        "first_name": member.get("first_name") if member else None,
        "last_name": member.get("last_name") if member else None,
    }


@router.post("/cohorts/{cohort_id}", response_model=MessageResponse)
async def send_cohort_message(
    cohort_id: uuid.UUID,
    message: CohortMessageCreate,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Send a message to all enrolled students in a cohort.

    Access control:
    - Coaches: Can send to their assigned cohorts
    - Admins: Can send to any cohort
    """
    # Get sender's member_id
    sender_member_id = await get_member_id_from_auth(current_user.user_id)

    # If not admin, validate coach owns the cohort
    if not is_admin_or_service(current_user):
        await validate_coach_owns_cohort(sender_member_id, cohort_id)

    # Get all enrolled students
    students = await get_cohort_enrolled_students(cohort_id)

    if not students:
        return MessageResponse(
            success=True,
            recipients_count=0,
            message="No enrolled students found in this cohort",
        )

    # Send branded emails to all students
    success_count = 0
    for student in students:
        email_sent = await send_message_email(
            to_email=student["email"],
            subject=message.subject,
            body=message.body,
        )
        if email_sent:
            success_count += 1

    # Log the message
    message_log = MessageLog(
        sender_id=sender_member_id,
        recipient_type=MessageRecipientType.COHORT,
        recipient_id=cohort_id,
        recipient_count=len(students),
        subject=message.subject,
        body=message.body,
    )
    db.add(message_log)
    await db.commit()

    return MessageResponse(
        success=True,
        recipients_count=len(students),
        message=f"Message sent to {success_count}/{len(students)} students",
    )


@router.post("/enrollments/{enrollment_id}", response_model=MessageResponse)
async def send_student_message(
    enrollment_id: uuid.UUID,
    message: StudentMessageCreate,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Send a message to an individual enrolled student.

    Access control:
    - Coaches: Can send to students in their assigned cohorts
    - Admins: Can send to any student
    """
    # Get sender's member_id
    sender_member_id = await get_member_id_from_auth(current_user.user_id)

    # Get student info
    student = await get_enrollment_student(enrollment_id)

    # If not admin, validate coach owns the cohort
    if not is_admin_or_service(current_user):
        await validate_coach_owns_cohort(sender_member_id, student["cohort_id"], db)

    # Send branded email
    email_sent = await send_message_email(
        to_email=student["email"],
        subject=message.subject,
        body=message.body,
    )

    # Log the message
    message_log = MessageLog(
        sender_id=sender_member_id,
        recipient_type=MessageRecipientType.STUDENT,
        recipient_id=enrollment_id,
        recipient_count=1,
        subject=message.subject,
        body=message.body,
    )
    db.add(message_log)
    await db.commit()

    return MessageResponse(
        success=email_sent,
        recipients_count=1,
        message="Message sent successfully" if email_sent else "Failed to send message",
    )


@router.get("/logs", response_model=List[MessageLogResponse])
async def list_message_logs(
    cohort_id: uuid.UUID = None,
    current_user: AuthUser = Depends(require_coach),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List sent message logs.

    Access control:
    - Coaches: See only their own sent messages
    - Admins: See all messages (optionally filtered by cohort)
    """
    # Get sender's member_id
    sender_member_id = await get_member_id_from_auth(current_user.user_id)

    query = select(MessageLog).order_by(MessageLog.sent_at.desc())

    # If not admin, filter to only own messages
    if not is_admin_or_service(current_user):
        query = query.where(MessageLog.sender_id == sender_member_id)

    # Optionally filter by cohort
    if cohort_id:
        query = query.where(
            MessageLog.recipient_type == MessageRecipientType.COHORT,
            MessageLog.recipient_id == cohort_id,
        )

    result = await db.execute(query)
    logs = result.scalars().all()

    # Get sender names
    responses = []
    for log in logs:
        sender = await get_member_by_id(
            str(log.sender_id), calling_service="communications"
        )
        sender_name = (
            f"{sender['first_name']} {sender['last_name']}" if sender else None
        )

        responses.append(
            MessageLogResponse(
                id=log.id,
                sender_id=log.sender_id,
                sender_name=sender_name,
                recipient_type=log.recipient_type.value,
                recipient_id=log.recipient_id,
                recipient_count=log.recipient_count,
                subject=log.subject,
                sent_at=log.sent_at,
            )
        )

    return responses
