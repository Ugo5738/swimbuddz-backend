"""HR-portal endpoints for browsing a company's programs and employees.

All endpoints scope queries by ``contact.id`` resolved from the bearer
token via ``require_corporate_admin``. Path parameters are never trusted
to identify the company — they only identify the program / employee
within the already-scoped set.

Read-only by design. HR contacts can view their cohort, see who's
enrolled, and pull the SwimBuddz Wrapped report; they cannot edit
employees, link cohorts, or move money.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.corporate_service.auth import require_corporate_admin
from services.corporate_service.models import (
    CorporateContact,
    CorporateProgram,
    CorporateProgramEmployee,
)
from services.corporate_service.schemas import (
    PortalEmployeeRow,
    PortalProgramSummary,
    ProgramOutcomeReportResponse,
)
from services.corporate_service.services.reports import (
    build_program_outcome_report,
    report_to_dict,
)

router = APIRouter(tags=["corporate-me"])


@router.get("/me", response_model=dict)
async def get_my_account(
    contact: CorporateContact = Depends(require_corporate_admin),
):
    """Identity hint for the portal frontend — used to render the header."""
    return {
        "contact_id": str(contact.id),
        "company_name": contact.company_name,
        "primary_contact_name": contact.primary_contact_name,
        "primary_contact_email": contact.primary_contact_email,
    }


@router.get("/me/programs", response_model=List[PortalProgramSummary])
async def list_my_programs(
    contact: CorporateContact = Depends(require_corporate_admin),
    db: AsyncSession = Depends(get_async_db),
) -> List[PortalProgramSummary]:
    """List all programs belonging to the caller's company."""
    rows = (
        (
            await db.execute(
                select(CorporateProgram)
                .where(CorporateProgram.contact_id == contact.id)
                .order_by(CorporateProgram.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        PortalProgramSummary(
            id=p.id,
            name=p.name,
            status=p.status,
            employee_count=p.employee_count,
            expected_start_date=p.expected_start_date.isoformat()
            if p.expected_start_date
            else None,
            expected_end_date=p.expected_end_date.isoformat()
            if p.expected_end_date
            else None,
            actual_start_date=p.actual_start_date.isoformat()
            if p.actual_start_date
            else None,
            actual_end_date=p.actual_end_date.isoformat()
            if p.actual_end_date
            else None,
        )
        for p in rows
    ]


async def _load_my_program(
    db: AsyncSession,
    contact: CorporateContact,
    program_id: uuid.UUID,
) -> CorporateProgram:
    program = (
        await db.execute(
            select(CorporateProgram).where(
                CorporateProgram.id == program_id,
                CorporateProgram.contact_id == contact.id,
            )
        )
    ).scalar_one_or_none()
    if not program:
        # 404 (not 403) so we don't reveal whether the ID exists for
        # another tenant. From the caller's perspective the program
        # simply doesn't exist.
        raise HTTPException(status_code=404, detail="Program not found")
    return program


@router.get(
    "/me/programs/{program_id}",
    response_model=PortalProgramSummary,
)
async def get_my_program(
    program_id: uuid.UUID,
    contact: CorporateContact = Depends(require_corporate_admin),
    db: AsyncSession = Depends(get_async_db),
) -> PortalProgramSummary:
    program = await _load_my_program(db, contact, program_id)
    return PortalProgramSummary(
        id=program.id,
        name=program.name,
        status=program.status,
        employee_count=program.employee_count,
        expected_start_date=program.expected_start_date.isoformat()
        if program.expected_start_date
        else None,
        expected_end_date=program.expected_end_date.isoformat()
        if program.expected_end_date
        else None,
        actual_start_date=program.actual_start_date.isoformat()
        if program.actual_start_date
        else None,
        actual_end_date=program.actual_end_date.isoformat()
        if program.actual_end_date
        else None,
    )


@router.get(
    "/me/programs/{program_id}/employees",
    response_model=List[PortalEmployeeRow],
)
async def list_my_program_employees(
    program_id: uuid.UUID,
    contact: CorporateContact = Depends(require_corporate_admin),
    db: AsyncSession = Depends(get_async_db),
) -> List[PortalEmployeeRow]:
    """HR can view their employee roster + status (no edits)."""
    program = await _load_my_program(db, contact, program_id)
    employees = (
        (
            await db.execute(
                select(CorporateProgramEmployee)
                .where(CorporateProgramEmployee.program_id == program.id)
                .order_by(CorporateProgramEmployee.full_name.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        PortalEmployeeRow(
            id=e.id,
            full_name=e.full_name,
            email=e.email,
            enrollment_status=e.enrollment_status.value,
            invitation_sent_at=e.invitation_sent_at,
            registered_at=e.registered_at,
            enrolled_at=e.enrolled_at,
        )
        for e in employees
    ]


@router.get(
    "/me/programs/{program_id}/report",
    response_model=ProgramOutcomeReportResponse,
)
async def get_my_program_report(
    program_id: uuid.UUID,
    contact: CorporateContact = Depends(require_corporate_admin),
    db: AsyncSession = Depends(get_async_db),
) -> ProgramOutcomeReportResponse:
    """Same SwimBuddz Wrapped report as the admin sees, scoped to the
    caller's company. Build is identical — only the auth gate differs."""
    program = await _load_my_program(db, contact, program_id)
    employees = (
        (
            await db.execute(
                select(CorporateProgramEmployee)
                .where(CorporateProgramEmployee.program_id == program.id)
                .order_by(CorporateProgramEmployee.full_name.asc())
            )
        )
        .scalars()
        .all()
    )
    report = await build_program_outcome_report(
        program, list(employees), company_name=contact.company_name
    )
    return ProgramOutcomeReportResponse.model_validate(report_to_dict(report))
