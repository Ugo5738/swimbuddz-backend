"""Admin endpoints for corporate program outcome reports (SwimBuddz Wrapped).

The report is built fresh on every request — there's no persisted report
table. The aggregation is I/O-bound (one fan-out to attendance_service +
academy_service per employee) so for big programs it can take a couple of
seconds, but staleness is worse than latency for this use case.

Endpoints live under /admin/corporate/programs/{id}/report and
/admin/corporate/programs/{id}/report/email.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateProgram,
    CorporateProgramEmployee,
    CorporateTouchpoint,
    TouchpointDirection,
    TouchpointType,
)
from services.corporate_service.schemas import (
    EmailReportRequest,
    EmailReportResponse,
    ProgramOutcomeReportResponse,
)
from services.corporate_service.services.reports import (
    build_program_outcome_report,
    report_to_dict,
)

logger = get_logger(__name__)
router = APIRouter(tags=["admin-corporate-reports"])


async def _load_program_with_context(
    db: AsyncSession, program_id: uuid.UUID
) -> tuple[CorporateProgram, CorporateContact, list[CorporateProgramEmployee]]:
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    contact = (
        await db.execute(
            select(CorporateContact).where(
                CorporateContact.id == program.contact_id
            )
        )
    ).scalar_one()

    employees = (
        (
            await db.execute(
                select(CorporateProgramEmployee)
                .where(CorporateProgramEmployee.program_id == program_id)
                .order_by(CorporateProgramEmployee.full_name.asc())
            )
        )
        .scalars()
        .all()
    )
    return program, contact, list(employees)


@router.get(
    "/programs/{program_id}/report",
    response_model=ProgramOutcomeReportResponse,
)
async def get_program_outcome_report(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> ProgramOutcomeReportResponse:
    """Build a fresh SwimBuddz Wrapped report for a program.

    Aggregates attendance + milestones per employee from attendance_service
    and academy_service. Safe to call as often as needed — there's no
    write side-effect; the touchpoint is only logged when the report is
    *emailed* via the sibling endpoint.
    """
    program, contact, employees = await _load_program_with_context(db, program_id)
    report = await build_program_outcome_report(
        program, employees, company_name=contact.company_name
    )
    return ProgramOutcomeReportResponse.model_validate(report_to_dict(report))


def _format_attendance_pct(rate: Optional[float]) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.0f}%"


def _build_email_body(
    *,
    contact_name: str,
    program_name: str,
    report_dict: dict,
    custom_note: Optional[str],
    report_url: Optional[str],
) -> tuple[str, str]:
    """Return ``(plain_text, html)`` versions of the email body."""
    attendance_pct = _format_attendance_pct(report_dict["aggregate_attendance_rate"])
    sessions_str = (
        f"{report_dict['aggregate_sessions_attended']}/"
        f"{report_dict['aggregate_sessions_possible']}"
    )
    milestones = report_dict["aggregate_milestones_achieved"]
    employees = report_dict["employee_count"]

    intro = (
        f"Hi {contact_name},\n\n"
        f"Here's the current SwimBuddz Wrapped snapshot for {program_name}.\n"
    )
    body_text = (
        intro
        + "\n"
        + f"  • Employees in cohort: {employees}\n"
        + f"  • Aggregate attendance: {attendance_pct} ({sessions_str} sessions)\n"
        + f"  • Milestones achieved across cohort: {milestones}\n"
    )
    if custom_note:
        body_text += f"\n{custom_note.strip()}\n"
    if report_url:
        body_text += f"\nView the full breakdown: {report_url}\n"
    body_text += (
        "\nLet me know if you'd like to walk through this on a call.\n\n"
        "— SwimBuddz"
    )

    html = (
        f"<p>Hi {contact_name},</p>"
        f"<p>Here's the current SwimBuddz Wrapped snapshot for "
        f"<strong>{program_name}</strong>.</p>"
        "<ul>"
        f"<li>Employees in cohort: <strong>{employees}</strong></li>"
        f"<li>Aggregate attendance: <strong>{attendance_pct}</strong> "
        f"({sessions_str} sessions)</li>"
        f"<li>Milestones achieved across cohort: "
        f"<strong>{milestones}</strong></li>"
        "</ul>"
    )
    if custom_note:
        html += f"<p>{custom_note.strip()}</p>"
    if report_url:
        html += (
            f'<p><a href="{report_url}">View the full breakdown</a></p>'
        )
    html += (
        "<p>Let me know if you'd like to walk through this on a call.</p>"
        "<p>— SwimBuddz</p>"
    )
    return body_text, html


@router.post(
    "/programs/{program_id}/report/email",
    response_model=EmailReportResponse,
)
async def email_program_outcome_report(
    program_id: uuid.UUID,
    payload: EmailReportRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
) -> EmailReportResponse:
    """Email the outcome snapshot to the program's HR contact.

    Logs a touchpoint of type ``email_followup_1`` (closest stock fit —
    "Wrapped report sent") on the contact so it shows up in their history.
    Returns ``delivered=False`` if the email send fails but still logs the
    attempt; the admin sees both via the returned payload.
    """
    program, contact, employees = await _load_program_with_context(db, program_id)
    report = await build_program_outcome_report(
        program, employees, company_name=contact.company_name
    )
    report_dict = report_to_dict(report)

    body_text, body_html = _build_email_body(
        contact_name=contact.primary_contact_name.split()[0]
        if contact.primary_contact_name
        else "there",
        program_name=program.name,
        report_dict=report_dict,
        custom_note=payload.custom_note,
        report_url=payload.report_url,
    )

    subject = f"SwimBuddz Wrapped — {program.name}"
    email_client = get_email_client()
    try:
        delivered = await email_client.send(
            to_email=contact.primary_contact_email,
            subject=subject,
            body=body_text,
            html_body=body_html,
        )
    except Exception:
        logger.warning(
            "Failed to email outcome report for program %s",
            program_id,
            exc_info=True,
        )
        delivered = False

    # Log a touchpoint either way — the attempt itself is useful history.
    summary = (
        f"Emailed outcome report to {contact.primary_contact_email}"
        if delivered
        else f"Attempted to email outcome report to {contact.primary_contact_email}"
        " (send failed)"
    )
    touchpoint = CorporateTouchpoint(
        contact_id=contact.id,
        type=TouchpointType.EMAIL_FOLLOWUP_1,
        direction=TouchpointDirection.OUTBOUND,
        occurred_at=utc_now(),
        summary=summary[:500],
        outcome=(
            f"Attendance: {_format_attendance_pct(report.aggregate_attendance_rate)} · "
            f"Milestones: {report.aggregate_milestones_achieved}"
        ),
        logged_by_auth_id=current_user.user_id,
    )
    db.add(touchpoint)
    await db.commit()
    await db.refresh(touchpoint)

    return EmailReportResponse(
        delivered=delivered,
        recipient_email=contact.primary_contact_email,
        touchpoint_id=touchpoint.id,
    )


# Silence unused-import warning while keeping datetime available for the
# eventual scheduled-report worker in Phase 4 (kept here intentionally).
_ = datetime
