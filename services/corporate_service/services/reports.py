"""Outcome report aggregation for corporate programs.

Phase 3 "SwimBuddz Wrapped" report — mid-cohort (week 6) and end-of-cohort
(week 12 + 1) snapshot sent to the HR contact. This module owns the
aggregation pipeline; the router thinly wraps it.

Data sources (all via service-role HTTP, never direct DB):
    - sessions_service: cohort session IDs
    - attendance_service: per-member attendance records (status counts)
    - academy_service: per-member milestones achieved / certificates earned

Cross-service IDs are passed as strings (UUIDs serialized) so JSON wire
formats line up cleanly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from services.corporate_service.models import (
    CorporateProgram,
    CorporateProgramEmployee,
    EmployeeEnrollmentStatus,
)
from services.corporate_service.services.clients import (
    get_cohort_session_ids,
    get_member_academy_summary,
    get_member_attendance_records,
)

# Statuses that count as "they showed up" for attendance %.
# (excused absences neither help nor hurt — excluded from denominator.)
_PRESENT_STATUSES = {"present", "late"}
_ABSENT_STATUSES = {"absent", "no_show"}


@dataclass
class EmployeeReport:
    """Per-employee row in the outcome report."""

    employee_id: str
    full_name: str
    email: str
    enrollment_status: EmployeeEnrollmentStatus
    sessions_attended: int
    sessions_total: int  # in-window sessions (excluded if neither present nor absent)
    attendance_rate: Optional[float]  # 0.0–1.0; None if 0 sessions counted
    milestones_achieved: int


@dataclass
class ProgramOutcomeReport:
    """Aggregated outcome report for a corporate program."""

    program_id: str
    program_name: str
    company_name: str
    status: str
    generated_at: datetime

    employee_count: int
    enrollment_funnel: dict[str, int]  # status → count

    sessions_in_cohort: int
    aggregate_sessions_attended: int
    aggregate_sessions_possible: int
    aggregate_attendance_rate: Optional[float]
    aggregate_milestones_achieved: int

    employees: list[EmployeeReport]

    # Report window — used for the academy summary call. Falls back to a
    # generous bracket around the program if dates aren't set yet.
    period_from: datetime
    period_to: datetime


def _summarise_attendance_records(records: list[dict]) -> tuple[int, int]:
    """Returns ``(attended, counted)``.

    `counted` excludes records whose status is neither present-ish nor
    absent-ish (e.g. ``excused``), so attendance % isn't punished for
    legitimately excused absences.
    """
    attended = 0
    counted = 0
    for r in records:
        status = (r.get("status") or "").lower()
        if status in _PRESENT_STATUSES:
            attended += 1
            counted += 1
        elif status in _ABSENT_STATUSES:
            counted += 1
    return attended, counted


def _report_window(program: CorporateProgram) -> tuple[datetime, datetime]:
    """Pick the date window for the academy-summary call.

    Order of preference: actual dates → expected dates → cohort lifetime
    fallback (1 year ago to now). The academy endpoint demands a from/to
    pair so we always supply something.
    """
    start: Optional[date] = program.actual_start_date or program.expected_start_date
    end: Optional[date] = program.actual_end_date or program.expected_end_date

    now = datetime.utcnow()
    if start is None:
        start_dt = datetime(now.year - 1, 1, 1)
    else:
        start_dt = datetime.combine(start, datetime.min.time())
    if end is None:
        end_dt = now
    else:
        end_dt = datetime.combine(end, datetime.max.time())
    return start_dt, end_dt


async def _fetch_employee_attendance_and_milestones(
    employee: CorporateProgramEmployee,
    session_ids: list[str],
    window_from: str,
    window_to: str,
) -> tuple[int, int, int]:
    """Returns ``(attended, counted, milestones)`` for a single employee.

    Skips the network entirely if the employee hasn't been matched to a
    member yet — those rows show as ``(0, 0, 0)`` and the report flags
    them via ``enrollment_status``.
    """
    if employee.member_id is None or employee.member_auth_id is None:
        return 0, 0, 0

    attendance_task = get_member_attendance_records(
        member_id=employee.member_id, session_ids=session_ids
    )
    summary_task = get_member_academy_summary(
        member_auth_id=employee.member_auth_id,
        date_from=window_from,
        date_to=window_to,
    )
    attendance_records, summary = await asyncio.gather(
        attendance_task, summary_task, return_exceptions=True
    )

    # Network errors are non-fatal — we just report 0s rather than failing
    # the whole report when one downstream blip happens.
    attended = 0
    counted = 0
    if isinstance(attendance_records, list):
        attended, counted = _summarise_attendance_records(attendance_records)

    milestones = 0
    if isinstance(summary, dict):
        milestones = int(summary.get("milestones_achieved", 0) or 0)

    return attended, counted, milestones


def _empty_funnel() -> dict[str, int]:
    return {s.value: 0 for s in EmployeeEnrollmentStatus}


async def build_program_outcome_report(
    program: CorporateProgram,
    employees: list[CorporateProgramEmployee],
    company_name: str,
) -> ProgramOutcomeReport:
    """Build a SwimBuddz Wrapped report for a corporate program.

    Pre-conditions: the program should already be linked to a cohort
    (``program.cohort_id`` set). If not, sessions_in_cohort will be 0 and
    every employee row will show 0 attendance — still useful as a "no data
    yet" status.
    """
    funnel = _empty_funnel()
    for emp in employees:
        funnel[emp.enrollment_status.value] += 1

    session_ids: list[str] = []
    if program.cohort_id is not None:
        try:
            session_ids = await get_cohort_session_ids(program.cohort_id)
        except Exception:
            # Treat cohort lookup failure as "no sessions" rather than 500
            # — admins can retry; partial reports beat no report.
            session_ids = []

    window_from_dt, window_to_dt = _report_window(program)
    window_from = window_from_dt.isoformat()
    window_to = window_to_dt.isoformat()

    rows: list[EmployeeReport] = []
    agg_attended = 0
    agg_counted = 0
    agg_milestones = 0

    # Fan out per-employee fetches in parallel — Python's asyncio handles
    # connection pooling via httpx clients spun up per call. The work is
    # I/O bound (2 HTTP calls per employee).
    fetch_tasks = [
        _fetch_employee_attendance_and_milestones(
            emp, session_ids, window_from, window_to
        )
        for emp in employees
    ]
    fetched = await asyncio.gather(*fetch_tasks)

    for emp, (attended, counted, milestones) in zip(employees, fetched):
        agg_attended += attended
        agg_counted += counted
        agg_milestones += milestones
        rows.append(
            EmployeeReport(
                employee_id=str(emp.id),
                full_name=emp.full_name,
                email=emp.email,
                enrollment_status=emp.enrollment_status,
                sessions_attended=attended,
                sessions_total=counted,
                attendance_rate=(attended / counted) if counted > 0 else None,
                milestones_achieved=milestones,
            )
        )

    aggregate_rate = (agg_attended / agg_counted) if agg_counted > 0 else None

    return ProgramOutcomeReport(
        program_id=str(program.id),
        program_name=program.name,
        company_name=company_name,
        status=program.status.value,
        generated_at=datetime.utcnow(),
        employee_count=len(employees),
        enrollment_funnel=funnel,
        sessions_in_cohort=len(session_ids),
        aggregate_sessions_attended=agg_attended,
        aggregate_sessions_possible=agg_counted,
        aggregate_attendance_rate=aggregate_rate,
        aggregate_milestones_achieved=agg_milestones,
        employees=rows,
        period_from=window_from_dt,
        period_to=window_to_dt,
    )


def report_to_dict(report: ProgramOutcomeReport) -> dict:
    """Serialise the dataclass to JSON-friendly dict for the API response."""
    return {
        "program_id": report.program_id,
        "program_name": report.program_name,
        "company_name": report.company_name,
        "status": report.status,
        "generated_at": report.generated_at.isoformat(),
        "period_from": report.period_from.isoformat(),
        "period_to": report.period_to.isoformat(),
        "employee_count": report.employee_count,
        "enrollment_funnel": report.enrollment_funnel,
        "sessions_in_cohort": report.sessions_in_cohort,
        "aggregate_sessions_attended": report.aggregate_sessions_attended,
        "aggregate_sessions_possible": report.aggregate_sessions_possible,
        "aggregate_attendance_rate": report.aggregate_attendance_rate,
        "aggregate_milestones_achieved": report.aggregate_milestones_achieved,
        "employees": [
            {
                "employee_id": r.employee_id,
                "full_name": r.full_name,
                "email": r.email,
                "enrollment_status": r.enrollment_status.value,
                "sessions_attended": r.sessions_attended,
                "sessions_total": r.sessions_total,
                "attendance_rate": r.attendance_rate,
                "milestones_achieved": r.milestones_achieved,
            }
            for r in report.employees
        ],
    }


# Convenience for the router to pick up the IDs needed for cross-service refs.
def cohort_id_of(program: CorporateProgram) -> Optional[UUID]:
    return program.cohort_id
