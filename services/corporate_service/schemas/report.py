"""Pydantic schemas for the corporate program outcome report (SwimBuddz Wrapped)."""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from services.corporate_service.models.enums import EmployeeEnrollmentStatus


class EmployeeReportRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee_id: uuid.UUID
    full_name: str
    email: str
    enrollment_status: EmployeeEnrollmentStatus
    sessions_attended: int
    sessions_total: int
    attendance_rate: Optional[float] = None
    milestones_achieved: int


class ProgramOutcomeReportResponse(BaseModel):
    """SwimBuddz Wrapped — outcome snapshot for one corporate program."""

    program_id: uuid.UUID
    program_name: str
    company_name: str
    status: str
    generated_at: datetime
    period_from: datetime
    period_to: datetime

    employee_count: int
    enrollment_funnel: dict[str, int]

    sessions_in_cohort: int
    aggregate_sessions_attended: int
    aggregate_sessions_possible: int
    aggregate_attendance_rate: Optional[float] = None
    aggregate_milestones_achieved: int

    employees: list[EmployeeReportRow]


class EmailReportRequest(BaseModel):
    """Send the outcome report URL to the program's HR contact.

    The URL points to the admin frontend's read-only view of the report —
    Phase 3.3 (HR portal) will add a real HR-facing view scoped to that
    contact's company; until then this just shares the admin URL via email,
    with a note. ``custom_note`` lets the admin add a personal line.
    """

    custom_note: Optional[str] = None
    report_url: Optional[str] = None  # full URL to embed in the email


class EmailReportResponse(BaseModel):
    delivered: bool
    recipient_email: str
    touchpoint_id: uuid.UUID
