"""Cohort timeline-shift endpoints (preview / apply / audit log listing).

Applies an idempotent timeline shift across cohort dates, sessions in
sessions-service, pending installments, and start-countdown reminders;
writes an immutable audit row per accepted apply.
"""

import asyncio
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.service_client import get_member_by_auth_id, get_members_bulk
from libs.db.session import get_async_db
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    CohortTimelineShiftLog,
    Enrollment,
    EnrollmentInstallment,
    InstallmentStatus,
)
from services.academy_service.routers._shared import (
    _COHORT_TIMELINE_NOTIFY_STATUSES,
    _START_COUNTDOWN_REMINDER_KEYS,
    _build_session_impacts,
    _build_shift_notice_body,
    _fetch_cohort_sessions_for_shift,
    _shift_sessions_or_raise,
    _timeline_shift_response_from_log,
    _to_utc,
    _updated_at_mismatch,
    _validate_shift_window,
)
from services.academy_service.schemas import (
    CohortTimelineSessionImpact,
    CohortTimelineShiftApplyResponse,
    CohortTimelineShiftLogResponse,
    CohortTimelineShiftPreviewResponse,
    CohortTimelineShiftRequest,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["academy"])


@router.post(
    "/cohorts/{cohort_id}/timeline-shifts/preview",
    response_model=CohortTimelineShiftPreviewResponse,
)
async def preview_cohort_timeline_shift(
    cohort_id: uuid.UUID,
    shift_in: CohortTimelineShiftRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Preview a cohort timeline shift without applying changes.

    This endpoint is intentionally side-effect free and reports:
    - date delta validation
    - session shiftability breakdown
    - pending installment count that would be rebased
    - reminder reset opportunities
    """
    query = select(Cohort).where(Cohort.id == cohort_id)
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    new_start_utc = _to_utc(shift_in.new_start_date)
    new_end_utc = _to_utc(shift_in.new_end_date)
    delta = _validate_shift_window(
        old_start=cohort.start_date,
        old_end=cohort.end_date,
        new_start=new_start_utc,
        new_end=new_end_utc,
    )
    already_applied = (
        _to_utc(cohort.start_date) == new_start_utc
        and _to_utc(cohort.end_date) == new_end_utc
    )
    if (
        _updated_at_mismatch(cohort.updated_at, shift_in.expected_updated_at)
        and not already_applied
    ):
        raise HTTPException(
            status_code=409,
            detail="Cohort was updated by another change. Refresh and retry.",
        )

    sessions: list[dict] = []
    impacts: list[CohortTimelineSessionImpact] = []
    shiftable = 0
    blocked = 0
    if shift_in.shift_sessions and not already_applied:
        sessions = await _fetch_cohort_sessions_for_shift(cohort_id)
        impacts, shiftable, blocked = _build_session_impacts(sessions, delta)

    pending_installments = 0
    if shift_in.shift_installments and not already_applied:
        pending_installments_result = await db.execute(
            select(func.count(EnrollmentInstallment.id))
            .join(Enrollment, Enrollment.id == EnrollmentInstallment.enrollment_id)
            .where(Enrollment.cohort_id == cohort_id)
            .where(EnrollmentInstallment.status == InstallmentStatus.PENDING)
        )
        pending_installments = pending_installments_result.scalar() or 0

    reminder_resets_possible = 0
    if shift_in.reset_start_reminders and not already_applied:
        enrollments_result = await db.execute(
            select(Enrollment).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(_COHORT_TIMELINE_NOTIFY_STATUSES),
            )
        )
        enrollments = enrollments_result.scalars().all()
        reminder_resets_possible = sum(
            1
            for enrollment in enrollments
            if set(enrollment.reminders_sent or []).intersection(
                _START_COUNTDOWN_REMINDER_KEYS
            )
        )

    return CohortTimelineShiftPreviewResponse(
        cohort_id=cohort_id,
        old_start_date=cohort.start_date,
        old_end_date=cohort.end_date,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        already_applied=already_applied,
        sessions_total=len(sessions),
        sessions_shiftable=shiftable,
        sessions_blocked=blocked,
        pending_installments=pending_installments,
        reminder_resets_possible=reminder_resets_possible,
        session_impacts=impacts,
    )


@router.post(
    "/cohorts/{cohort_id}/timeline-shifts",
    response_model=CohortTimelineShiftApplyResponse,
)
async def apply_cohort_timeline_shift(
    cohort_id: uuid.UUID,
    shift_in: CohortTimelineShiftRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Apply a cohort timeline shift and propagate it across linked records.

    Workflow:
    1. Validate equal start/end delta (duration preserved)
    2. Shift eligible sessions in sessions-service (with compensation on failure)
    3. Shift pending installment due dates
    4. Reset enrollment countdown reminders
    5. Persist cohort dates and send member notifications (best effort)
    """
    # Serialize timeline-shift operations per cohort to avoid concurrent
    # double-apply races from duplicate submits/retries.
    query = select(Cohort).where(Cohort.id == cohort_id).with_for_update()
    result = await db.execute(query)
    cohort = result.scalar_one_or_none()
    if not cohort:
        raise HTTPException(status_code=404, detail="Cohort not found")

    idempotency_key = (
        shift_in.idempotency_key.strip() if shift_in.idempotency_key else None
    )
    if idempotency_key:
        existing_log_result = await db.execute(
            select(CohortTimelineShiftLog).where(
                CohortTimelineShiftLog.cohort_id == cohort_id,
                CohortTimelineShiftLog.idempotency_key == idempotency_key,
            )
        )
        existing_log = existing_log_result.scalar_one_or_none()
        if existing_log:
            return _timeline_shift_response_from_log(existing_log)

    if cohort.status in {CohortStatus.COMPLETED, CohortStatus.CANCELLED}:
        raise HTTPException(
            status_code=400,
            detail="Cannot timeline-shift completed or cancelled cohorts",
        )

    new_start_utc = _to_utc(shift_in.new_start_date)
    new_end_utc = _to_utc(shift_in.new_end_date)
    delta = _validate_shift_window(
        old_start=cohort.start_date,
        old_end=cohort.end_date,
        new_start=new_start_utc,
        new_end=new_end_utc,
    )
    old_start = cohort.start_date
    old_end = cohort.end_date

    already_applied = (
        _to_utc(old_start) == new_start_utc and _to_utc(old_end) == new_end_utc
    )
    if (
        _updated_at_mismatch(cohort.updated_at, shift_in.expected_updated_at)
        and not already_applied
    ):
        raise HTTPException(
            status_code=409,
            detail="Cohort was updated by another change. Refresh and retry.",
        )
    if already_applied:
        response = CohortTimelineShiftApplyResponse(
            cohort_id=cohort_id,
            old_start_date=old_start,
            old_end_date=old_end,
            new_start_date=new_start_utc,
            new_end_date=new_end_utc,
            delta_seconds=int(delta.total_seconds()),
            already_applied=True,
        )
        if idempotency_key:
            log_row = CohortTimelineShiftLog(
                cohort_id=cohort_id,
                idempotency_key=idempotency_key,
                actor_auth_id=current_user.user_id,
                reason=shift_in.reason,
                old_start_date=old_start,
                old_end_date=old_end,
                new_start_date=new_start_utc,
                new_end_date=new_end_utc,
                delta_seconds=int(delta.total_seconds()),
                options_json={
                    "shift_sessions": bool(shift_in.shift_sessions),
                    "shift_installments": bool(shift_in.shift_installments),
                    "reset_start_reminders": bool(shift_in.reset_start_reminders),
                    "notify_members": bool(shift_in.notify_members),
                    "set_status_to_open_if_future": bool(
                        shift_in.set_status_to_open_if_future
                    ),
                },
                results_json={"already_applied": True},
                warnings=[],
            )
            db.add(log_row)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                existing_log_result = await db.execute(
                    select(CohortTimelineShiftLog).where(
                        CohortTimelineShiftLog.cohort_id == cohort_id,
                        CohortTimelineShiftLog.idempotency_key == idempotency_key,
                    )
                )
                existing_log = existing_log_result.scalar_one_or_none()
                if existing_log:
                    return _timeline_shift_response_from_log(existing_log)
        return response

    session_impacts: list[CohortTimelineSessionImpact] = []
    sessions_shifted = 0
    sessions_skipped = 0
    warnings: list[str] = []
    if shift_in.shift_sessions:
        sessions = await _fetch_cohort_sessions_for_shift(cohort_id)
        session_impacts, _, _ = _build_session_impacts(sessions, delta)
        (
            sessions_shifted,
            sessions_skipped,
            session_warnings,
        ) = await _shift_sessions_or_raise(impacts=session_impacts)
        warnings.extend(session_warnings)

    pending_installments_shifted = 0
    reminder_resets_applied = 0
    notify_enrollments: list[Enrollment] = []

    cohort.start_date = new_start_utc
    cohort.end_date = new_end_utc

    now = utc_now()
    if (
        shift_in.set_status_to_open_if_future
        and new_start_utc > now
        and cohort.status == CohortStatus.ACTIVE
    ):
        cohort.status = CohortStatus.OPEN

    if shift_in.shift_installments:
        pending_installments_result = await db.execute(
            select(EnrollmentInstallment)
            .join(Enrollment, Enrollment.id == EnrollmentInstallment.enrollment_id)
            .where(Enrollment.cohort_id == cohort_id)
            .where(EnrollmentInstallment.status == InstallmentStatus.PENDING)
        )
        pending_installments = pending_installments_result.scalars().all()
        for installment in pending_installments:
            installment.due_at = installment.due_at + delta
        pending_installments_shifted = len(pending_installments)

    if shift_in.reset_start_reminders or shift_in.notify_members:
        enrollment_result = await db.execute(
            select(Enrollment).where(
                Enrollment.cohort_id == cohort_id,
                Enrollment.status.in_(_COHORT_TIMELINE_NOTIFY_STATUSES),
            )
        )
        notify_enrollments = enrollment_result.scalars().all()

    if shift_in.reset_start_reminders:
        for enrollment in notify_enrollments:
            existing = list(enrollment.reminders_sent or [])
            filtered = [
                key for key in existing if key not in _START_COUNTDOWN_REMINDER_KEYS
            ]
            if filtered != existing:
                enrollment.reminders_sent = filtered
                reminder_resets_applied += 1

    await db.commit()
    await db.refresh(cohort)

    notification_attempts = 0
    notification_sent = 0
    if shift_in.notify_members and notify_enrollments:
        try:
            member_ids = list(
                {str(e.member_id) for e in notify_enrollments if e.member_id}
            )
            member_map = {
                member["id"]: member
                for member in await get_members_bulk(
                    member_ids, calling_service="academy"
                )
            }
            email_client = get_email_client()

            async def _send_notice(member_payload: dict) -> bool:
                full_name = (
                    f"{member_payload.get('first_name', '')} {member_payload.get('last_name', '')}"
                ).strip() or "Swimmer"
                return await email_client.send(
                    to_email=member_payload["email"],
                    subject=f"Schedule updated: {cohort.name}",
                    body=_build_shift_notice_body(
                        member_name=full_name,
                        cohort_name=cohort.name,
                        old_start=old_start,
                        old_end=old_end,
                        new_start=new_start_utc,
                        new_end=new_end_utc,
                        reason=shift_in.reason,
                    ),
                )

            send_coroutines = []
            for member in member_map.values():
                if member.get("email"):
                    notification_attempts += 1
                    send_coroutines.append(_send_notice(member))

            if send_coroutines:
                send_results = await asyncio.gather(
                    *send_coroutines, return_exceptions=True
                )
                for result in send_results:
                    if result is True:
                        notification_sent += 1
                    elif isinstance(result, Exception):
                        warnings.append(f"Member notification error: {result}")
        except Exception as exc:
            warnings.append(
                f"Member notifications skipped due to member lookup/send failure: {exc}"
            )

    actor_member_id = None
    try:
        actor_member = await get_member_by_auth_id(
            current_user.user_id, calling_service="academy"
        )
        if actor_member:
            actor_member_id = actor_member.get("id")
    except Exception as exc:
        warnings.append(f"Could not resolve actor member for audit log: {exc}")

    log_row = CohortTimelineShiftLog(
        cohort_id=cohort_id,
        idempotency_key=idempotency_key,
        actor_auth_id=current_user.user_id,
        actor_member_id=actor_member_id,
        reason=shift_in.reason,
        old_start_date=old_start,
        old_end_date=old_end,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        options_json={
            "shift_sessions": bool(shift_in.shift_sessions),
            "shift_installments": bool(shift_in.shift_installments),
            "reset_start_reminders": bool(shift_in.reset_start_reminders),
            "notify_members": bool(shift_in.notify_members),
            "set_status_to_open_if_future": bool(shift_in.set_status_to_open_if_future),
        },
        results_json={
            "already_applied": False,
            "sessions_shifted": sessions_shifted,
            "sessions_skipped": sessions_skipped,
            "pending_installments_shifted": pending_installments_shifted,
            "reminder_resets_applied": reminder_resets_applied,
            "notification_attempts": notification_attempts,
            "notification_sent": notification_sent,
        },
        warnings=warnings,
    )
    db.add(log_row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing_log_result = await db.execute(
                select(CohortTimelineShiftLog).where(
                    CohortTimelineShiftLog.cohort_id == cohort_id,
                    CohortTimelineShiftLog.idempotency_key == idempotency_key,
                )
            )
            existing_log = existing_log_result.scalar_one_or_none()
            if existing_log:
                return _timeline_shift_response_from_log(existing_log)
        warnings.append("Audit log write failed due to idempotency conflict")

    return CohortTimelineShiftApplyResponse(
        cohort_id=cohort_id,
        old_start_date=old_start,
        old_end_date=old_end,
        new_start_date=new_start_utc,
        new_end_date=new_end_utc,
        delta_seconds=int(delta.total_seconds()),
        already_applied=False,
        sessions_shifted=sessions_shifted,
        sessions_skipped=sessions_skipped,
        pending_installments_shifted=pending_installments_shifted,
        reminder_resets_applied=reminder_resets_applied,
        notification_attempts=notification_attempts,
        notification_sent=notification_sent,
        warnings=warnings,
    )


@router.get(
    "/cohorts/{cohort_id}/timeline-shifts",
    response_model=List[CohortTimelineShiftLogResponse],
)
async def list_cohort_timeline_shift_logs(
    cohort_id: uuid.UUID,
    limit: int = 20,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List immutable timeline-shift audit logs for a cohort (newest first)."""
    capped_limit = max(1, min(limit, 100))
    result = await db.execute(
        select(CohortTimelineShiftLog)
        .where(CohortTimelineShiftLog.cohort_id == cohort_id)
        .order_by(CohortTimelineShiftLog.created_at.desc())
        .limit(capped_limit)
    )
    return result.scalars().all()
