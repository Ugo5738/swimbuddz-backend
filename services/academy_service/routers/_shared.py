import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from libs.auth.dependencies import (
    get_current_user,
    require_admin,
    require_coach,
    require_coach_for_cohort,
)
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import kobo_to_bubbles
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.media_utils import resolve_media_url, resolve_media_urls
from libs.common.pdf import generate_certificate_pdf, generate_progress_report_pdf
from libs.common.service_client import (
    debit_member_wallet,
    get_coach_profile,
    get_eligible_coaches,
    get_member_by_auth_id,
    get_member_by_id,
    get_members_bulk,
    get_next_session_for_cohort,
    internal_delete,
    internal_get,
    internal_patch,
    internal_post,
)
from libs.db.session import get_async_db
from services.academy_service.models import (
    CoachAssignment,
    CoachGrade,
    Cohort,
    CohortComplexityScore,
    CohortResource,
    CohortStatus,
    CohortTimelineShiftLog,
    Enrollment,
    EnrollmentInstallment,
    EnrollmentStatus,
    InstallmentStatus,
    Milestone,
    PaymentStatus,
    Program,
    ProgramCategory,
    ProgramInterest,
    ProgressStatus,
    StudentProgress,
)
from services.academy_service.schemas import (
    AdminDropoutActionRequest,
    AICoachSuggestion,
    AICoachSuggestionResponse,
    AIDimensionSuggestion,
    AIScoringRequest,
    AIScoringResponse,
    CoachCohortDetail,
    CoachDashboardSummary,
    CohortComplexityScoreCreate,
    CohortComplexityScoreResponse,
    CohortComplexityScoreUpdate,
    CohortCreate,
    CohortResourceResponse,
    CohortResponse,
    CohortTimelineSessionImpact,
    CohortTimelineShiftApplyResponse,
    CohortTimelineShiftLogResponse,
    CohortTimelineShiftPreviewResponse,
    CohortTimelineShiftRequest,
    CohortUpdate,
    ComplexityScoreCalculateRequest,
    ComplexityScoreCalculation,
    DimensionLabelsResponse,
    EligibleCoachResponse,
    EnrollmentCreate,
    EnrollmentMarkPaidRequest,
    EnrollmentResponse,
    EnrollmentUpdate,
    MemberMilestoneClaimRequest,
    MilestoneCreate,
    MilestoneResponse,
    MilestoneReviewAction,
    NextSessionInfo,
    OnboardingResponse,
    PendingMilestoneReview,
    ProgramCreate,
    ProgramResponse,
    ProgramUpdate,
    StudentProgressResponse,
    StudentProgressUpdate,
    UpcomingSessionSummary,
)
from services.academy_service.services.installments import (
    build_schedule,
    mark_overdue_installments,
    sync_enrollment_installment_state,
)
from services.academy_service.services.scoring import (
    calculate_complexity_score,
    get_dimension_labels,
)
from services.academy_service.tasks import transition_cohort_statuses
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

# Explicitly export private helpers so `from _shared import *` includes them.
# Python's import-star skips names starting with `_` unless listed in __all__.
__all__ = [
    # ── stdlib / third-party re-exports ───────────────────────────────────
    "asyncio",
    "uuid",
    "datetime",
    "timedelta",
    "timezone",
    "List",
    "Optional",
    "APIRouter",
    "Depends",
    "HTTPException",
    "status",
    "Response",
    "get_current_user",
    "require_admin",
    "require_coach",
    "require_coach_for_cohort",
    "AuthUser",
    "get_settings",
    "kobo_to_bubbles",
    "utc_now",
    "get_email_client",
    "get_logger",
    "resolve_media_url",
    "resolve_media_urls",
    "generate_certificate_pdf",
    "generate_progress_report_pdf",
    "debit_member_wallet",
    "get_coach_profile",
    "get_eligible_coaches",
    "get_member_by_auth_id",
    "get_member_by_id",
    "get_members_bulk",
    "get_next_session_for_cohort",
    "internal_delete",
    "internal_get",
    "internal_patch",
    "internal_post",
    "get_async_db",
    # ── models ────────────────────────────────────────────────────────────
    "CoachAssignment",
    "CoachGrade",
    "Cohort",
    "CohortComplexityScore",
    "CohortResource",
    "CohortStatus",
    "CohortTimelineShiftLog",
    "Enrollment",
    "EnrollmentInstallment",
    "EnrollmentStatus",
    "InstallmentStatus",
    "Milestone",
    "PaymentStatus",
    "Program",
    "ProgramCategory",
    "ProgramInterest",
    "ProgressStatus",
    "StudentProgress",
    # ── schemas ───────────────────────────────────────────────────────────
    "AdminDropoutActionRequest",
    "AICoachSuggestion",
    "AICoachSuggestionResponse",
    "AIDimensionSuggestion",
    "AIScoringRequest",
    "AIScoringResponse",
    "CoachCohortDetail",
    "CoachDashboardSummary",
    "CohortComplexityScoreCreate",
    "CohortComplexityScoreResponse",
    "CohortComplexityScoreUpdate",
    "CohortCreate",
    "CohortResourceResponse",
    "CohortResponse",
    "CohortTimelineSessionImpact",
    "CohortTimelineShiftApplyResponse",
    "CohortTimelineShiftLogResponse",
    "CohortTimelineShiftPreviewResponse",
    "CohortTimelineShiftRequest",
    "CohortUpdate",
    "ComplexityScoreCalculateRequest",
    "ComplexityScoreCalculation",
    "DimensionLabelsResponse",
    "EligibleCoachResponse",
    "EnrollmentCreate",
    "EnrollmentMarkPaidRequest",
    "EnrollmentResponse",
    "EnrollmentUpdate",
    "MemberMilestoneClaimRequest",
    "MilestoneCreate",
    "MilestoneResponse",
    "MilestoneReviewAction",
    "NextSessionInfo",
    "OnboardingResponse",
    "PendingMilestoneReview",
    "ProgramCreate",
    "ProgramResponse",
    "ProgramUpdate",
    "StudentProgressResponse",
    "StudentProgressUpdate",
    "UpcomingSessionSummary",
    # ── services ──────────────────────────────────────────────────────────
    "build_schedule",
    "mark_overdue_installments",
    "sync_enrollment_installment_state",
    "calculate_complexity_score",
    "get_dimension_labels",
    "transition_cohort_statuses",
    # ── sqlalchemy ────────────────────────────────────────────────────────
    "and_",
    "delete",
    "func",
    "or_",
    "select",
    "IntegrityError",
    "AsyncSession",
    "joinedload",
    "selectinload",
    # ── module-level constants (private names) ────────────────────────────
    "_SHIFTABLE_SESSION_STATUSES",
    "_START_COUNTDOWN_REMINDER_KEYS",
    "_COHORT_TIMELINE_NOTIFY_STATUSES",
    "_GRADE_COLUMN_MAP",
    # ── private helper functions ──────────────────────────────────────────
    "_ensure_active_coach",
    "_resolve_enrollment_total_fee",
    "_list_enrollment_installments",
    "_ensure_installment_plan",
    "_sync_installment_state_for_enrollment",
    "_to_utc",
    "_parse_iso_datetime",
    "_validate_shift_window",
    "_fetch_cohort_sessions_for_shift",
    "_build_session_impacts",
    "_shift_sessions_or_raise",
    "_format_date_for_notice",
    "_updated_at_mismatch",
    "_build_shift_notice_body",
    "_timeline_shift_response_from_log",
    "_is_mid_entry_open_now",
]

_SHIFTABLE_SESSION_STATUSES = {"draft", "scheduled", "in_progress"}
_START_COUNTDOWN_REMINDER_KEYS = {"7_days", "3_days", "1_days"}
_COHORT_TIMELINE_NOTIFY_STATUSES = {
    EnrollmentStatus.ENROLLED,
    EnrollmentStatus.PENDING_APPROVAL,
}

_GRADE_COLUMN_MAP = {
    ProgramCategory.LEARN_TO_SWIM: "learn_to_swim_grade",
    ProgramCategory.SPECIAL_POPULATIONS: "special_populations_grade",
    ProgramCategory.INSTITUTIONAL: "institutional_grade",
    ProgramCategory.COMPETITIVE_ELITE: "competitive_elite_grade",
    ProgramCategory.CERTIFICATIONS: "certifications_grade",
    ProgramCategory.SPECIALIZED_DISCIPLINES: "specialized_disciplines_grade",
    ProgramCategory.ADJACENT_SERVICES: "adjacent_services_grade",
}


async def _ensure_active_coach(coach_member_id: uuid.UUID) -> None:
    profile = await get_coach_profile(str(coach_member_id), calling_service="academy")
    if profile is None:
        raise HTTPException(status_code=400, detail="Coach profile not found")
    if profile["status"] != "active":
        raise HTTPException(
            status_code=400,
            detail="Coach must complete onboarding before assignment",
        )


def _resolve_enrollment_total_fee(program: Program, cohort: Cohort | None) -> int:
    if cohort and cohort.price_override is not None:
        return int(cohort.price_override)
    return int(program.price_amount or 0)


async def _list_enrollment_installments(
    db: AsyncSession, enrollment_id: uuid.UUID
) -> list[EnrollmentInstallment]:
    result = await db.execute(
        select(EnrollmentInstallment)
        .where(EnrollmentInstallment.enrollment_id == enrollment_id)
        .order_by(EnrollmentInstallment.installment_number.asc())
    )
    return result.scalars().all()


async def _ensure_installment_plan(
    db: AsyncSession,
    enrollment: Enrollment,
    program: Program | None,
    cohort: Cohort | None,
    *,
    use_installments: bool = False,
) -> list[EnrollmentInstallment]:
    """
    Build the installment schedule for an enrollment if it doesn't exist yet.

    Schedule is only created when:
    - enrollment.uses_installments=True (persisted member opt-in), AND
    - ``cohort.installment_plan_enabled=True`` (admin enabled it for this cohort).

    If installments already exist they are returned as-is regardless of the flag,
    so re-fetching the enrollment never accidentally clears an existing plan.
    """
    # Persist installment preference once the member explicitly opts in at checkout.
    if use_installments and not enrollment.uses_installments:
        enrollment.uses_installments = True

    installments = await _list_enrollment_installments(db, enrollment.id)
    if installments:
        return installments

    # Only build a schedule when both the cohort supports it AND the member opted in.
    if not enrollment.uses_installments or not getattr(
        cohort, "installment_plan_enabled", False
    ):
        return []

    # Fully paid enrollments should never create installment obligations.
    if enrollment.payment_status == PaymentStatus.PAID:
        return []

    if not program or not cohort:
        return []

    total_fee = _resolve_enrollment_total_fee(program, cohort)
    if total_fee <= 0:
        return []

    # Apply cohort-level overrides if set, otherwise let build_schedule auto-compute.
    count_override: int | None = getattr(cohort, "installment_count", None)
    deposit_override: int | None = getattr(cohort, "installment_deposit_amount", None)

    try:
        schedule = build_schedule(
            total_fee=total_fee,
            duration_weeks=int(program.duration_weeks),
            cohort_start=cohort.start_date,
            count_override=count_override,
            deposit_override=deposit_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    for item in schedule:
        db.add(
            EnrollmentInstallment(
                enrollment_id=enrollment.id,
                installment_number=item["installment_number"],
                amount=item["amount"],
                due_at=item["due_at"],
                status=InstallmentStatus.PENDING,
            )
        )

    enrollment.price_snapshot_amount = total_fee
    enrollment.currency_snapshot = program.currency or "NGN"
    await db.flush()
    return await _list_enrollment_installments(db, enrollment.id)


async def _sync_installment_state_for_enrollment(
    db: AsyncSession,
    enrollment: Enrollment,
    *,
    now_dt=None,
    use_installments: bool = False,
) -> list[EnrollmentInstallment]:
    cohort = enrollment.__dict__.get("cohort")
    program = enrollment.__dict__.get("program")

    if cohort is None and enrollment.cohort_id:
        cohort = (
            (
                await db.execute(
                    select(Cohort)
                    .where(Cohort.id == enrollment.cohort_id)
                    .options(selectinload(Cohort.program))
                )
            )
            .scalars()
            .first()
        )
        enrollment.cohort = cohort

    if program is None and cohort is not None:
        program = cohort.__dict__.get("program")

    if program is None and enrollment.program_id:
        program = (
            (
                await db.execute(
                    select(Program).where(Program.id == enrollment.program_id)
                )
            )
            .scalars()
            .first()
        )
        enrollment.program = program

    installments = await _ensure_installment_plan(
        db, enrollment, program, cohort, use_installments=use_installments
    )
    if not installments or not cohort or not program:
        return installments

    effective_now = now_dt or utc_now()
    mark_overdue_installments(installments, now=effective_now)
    sync_enrollment_installment_state(
        enrollment=enrollment,
        installments=installments,
        duration_weeks=int(program.duration_weeks),
        cohort_start=cohort.start_date,
        cohort_requires_approval=bool(cohort.require_approval),
        admin_dropout_approval=bool(cohort.admin_dropout_approval),
        now=effective_now,
    )
    return installments


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _validate_shift_window(
    *,
    old_start: datetime,
    old_end: datetime,
    new_start: datetime,
    new_end: datetime,
) -> timedelta:
    old_start_utc = _to_utc(old_start)
    old_end_utc = _to_utc(old_end)
    new_start_utc = _to_utc(new_start)
    new_end_utc = _to_utc(new_end)

    if new_end_utc <= new_start_utc:
        raise HTTPException(
            status_code=400,
            detail="new_end_date must be after new_start_date",
        )

    delta_start = new_start_utc - old_start_utc
    delta_end = new_end_utc - old_end_utc
    if delta_start != delta_end:
        raise HTTPException(
            status_code=400,
            detail=(
                "start_date and end_date must shift by the same amount "
                "(duration cannot change in timeline-shift mode)"
            ),
        )
    return delta_start


async def _fetch_cohort_sessions_for_shift(cohort_id: uuid.UUID) -> list[dict]:
    settings = get_settings()
    response = await internal_get(
        service_url=settings.SESSIONS_SERVICE_URL,
        # sessions-service list endpoint is mounted at "/sessions/".
        # Calling "/sessions" causes a 307 redirect, which we treat as non-success.
        path="/sessions/",
        calling_service="academy",
        params={"cohort_id": str(cohort_id), "include_drafts": "true"},
        timeout=20.0,
    )
    if not response.is_success:
        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to fetch cohort sessions from sessions service "
                f"(status={response.status_code})"
            ),
        )
    data = response.json()
    if not isinstance(data, list):
        raise HTTPException(
            status_code=502,
            detail="Invalid sessions-service response while preparing timeline shift",
        )
    return data


def _build_session_impacts(
    sessions: list[dict], delta: timedelta
) -> tuple[list[CohortTimelineSessionImpact], int, int]:
    impacts: list[CohortTimelineSessionImpact] = []
    shiftable = 0
    blocked = 0

    for raw in sessions:
        status_raw = (raw.get("status") or "").lower()
        starts_at = _parse_iso_datetime(str(raw["starts_at"]))
        ends_at = _parse_iso_datetime(str(raw["ends_at"]))
        will_shift = status_raw in _SHIFTABLE_SESSION_STATUSES
        if will_shift:
            shiftable += 1
        else:
            blocked += 1

        impacts.append(
            CohortTimelineSessionImpact(
                session_id=str(raw["id"]),
                status=status_raw,
                starts_at=starts_at,
                ends_at=ends_at,
                new_starts_at=starts_at + delta,
                new_ends_at=ends_at + delta,
                will_shift=will_shift,
            )
        )

    return impacts, shiftable, blocked


async def _shift_sessions_or_raise(
    *,
    impacts: list[CohortTimelineSessionImpact],
) -> tuple[int, int, list[str]]:
    settings = get_settings()
    shifted_count = 0
    skipped_count = 0
    warnings: list[str] = []
    patched_sessions: list[CohortTimelineSessionImpact] = []

    for impact in impacts:
        if not impact.will_shift:
            skipped_count += 1
            continue

        response = await internal_patch(
            service_url=settings.SESSIONS_SERVICE_URL,
            path=f"/sessions/{impact.session_id}",
            calling_service="academy",
            json={
                "starts_at": impact.new_starts_at.isoformat(),
                "ends_at": impact.new_ends_at.isoformat(),
            },
            timeout=20.0,
        )
        if response.is_success:
            shifted_count += 1
            patched_sessions.append(impact)
            continue

        rollback_failed: list[str] = []
        for applied in reversed(patched_sessions):
            rollback_resp = await internal_patch(
                service_url=settings.SESSIONS_SERVICE_URL,
                path=f"/sessions/{applied.session_id}",
                calling_service="academy",
                json={
                    "starts_at": applied.starts_at.isoformat(),
                    "ends_at": applied.ends_at.isoformat(),
                },
                timeout=20.0,
            )
            if not rollback_resp.is_success:
                rollback_failed.append(applied.session_id)

        if rollback_failed:
            warnings.append(
                "Rollback failed for sessions after patch error: "
                + ", ".join(rollback_failed)
            )

        raise HTTPException(
            status_code=502,
            detail=(
                "Failed to shift session timeline "
                f"(session_id={impact.session_id}, status={response.status_code})"
            ),
        )

    return shifted_count, skipped_count, warnings


def _format_date_for_notice(value: datetime) -> str:
    return _to_utc(value).strftime("%B %d, %Y %H:%M UTC")


def _updated_at_mismatch(current: datetime, expected: datetime | None) -> bool:
    if expected is None:
        return False
    return _to_utc(current) != _to_utc(expected)


def _build_shift_notice_body(
    *,
    member_name: str,
    cohort_name: str,
    old_start: datetime,
    old_end: datetime,
    new_start: datetime,
    new_end: datetime,
    reason: str | None,
) -> str:
    lines = [
        f"Hi {member_name},",
        "",
        f"We've updated the schedule for your cohort: {cohort_name}.",
        "",
        f"Previous dates: {_format_date_for_notice(old_start)} to {_format_date_for_notice(old_end)}",
        f"New dates: {_format_date_for_notice(new_start)} to {_format_date_for_notice(new_end)}",
    ]
    if reason:
        lines.extend(["", f"Reason: {reason}"])
    lines.extend(
        [
            "",
            "Your enrollment remains active. Please check your dashboard for the updated timeline.",
            "",
            "SwimBuddz Team",
        ]
    )
    return "\n".join(lines)


def _timeline_shift_response_from_log(
    log_row: CohortTimelineShiftLog,
) -> CohortTimelineShiftApplyResponse:
    results = dict(log_row.results_json or {})
    warnings = list(log_row.warnings or [])
    return CohortTimelineShiftApplyResponse(
        cohort_id=log_row.cohort_id,
        old_start_date=log_row.old_start_date,
        old_end_date=log_row.old_end_date,
        new_start_date=log_row.new_start_date,
        new_end_date=log_row.new_end_date,
        delta_seconds=log_row.delta_seconds,
        already_applied=bool(results.get("already_applied", False)),
        sessions_shifted=int(results.get("sessions_shifted", 0)),
        sessions_skipped=int(results.get("sessions_skipped", 0)),
        pending_installments_shifted=int(
            results.get("pending_installments_shifted", 0)
        ),
        reminder_resets_applied=int(results.get("reminder_resets_applied", 0)),
        notification_attempts=int(results.get("notification_attempts", 0)),
        notification_sent=int(results.get("notification_sent", 0)),
        warnings=warnings,
    )


def _is_mid_entry_open_now(cohort: Cohort, now_dt) -> bool:
    if cohort.status != CohortStatus.ACTIVE:
        return False
    if not cohort.allow_mid_entry:
        return False

    days_since_start = (now_dt - cohort.start_date).days
    current_week = (days_since_start // 7) + 1
    return current_week <= cohort.mid_entry_cutoff_week
