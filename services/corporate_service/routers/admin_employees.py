"""Admin endpoints for the employee manifest of a CorporateProgram."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateProgram,
    CorporateProgramEmployee,
    EmployeeEnrollmentStatus,
)
from services.corporate_service.schemas import (
    CorporateProgramEmployeeResponse,
    EmployeeBulkAddRequest,
    EmployeeBulkAddResponse,
    MatchMembersResponse,
)
from services.corporate_service.services.clients import find_member_by_email

router = APIRouter(tags=["admin-corporate-employees"])


@router.get(
    "/programs/{program_id}/employees",
    response_model=List[CorporateProgramEmployeeResponse],
)
async def list_employees(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List the employee manifest for a program."""
    program_exists = (
        await db.execute(
            select(CorporateProgram.id).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program_exists:
        raise HTTPException(status_code=404, detail="Program not found")

    result = await db.execute(
        select(CorporateProgramEmployee)
        .where(CorporateProgramEmployee.program_id == program_id)
        .order_by(CorporateProgramEmployee.created_at.asc())
    )
    return list(result.scalars().all())


@router.post(
    "/programs/{program_id}/employees",
    response_model=EmployeeBulkAddResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bulk_add_employees(
    program_id: uuid.UUID,
    payload: EmployeeBulkAddRequest,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk-add employees to a program (idempotent on email per program)."""
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Pre-load existing emails to avoid IntegrityError per row.
    existing_emails: set[str] = set(
        e.lower()
        for e in (
            await db.execute(
                select(CorporateProgramEmployee.email).where(
                    CorporateProgramEmployee.program_id == program_id
                )
            )
        )
        .scalars()
        .all()
    )

    added_rows: list[CorporateProgramEmployee] = []
    skipped = 0

    # Dedupe within the request itself too.
    seen_in_payload: set[str] = set()

    for row in payload.employees:
        email_norm = row.email.lower()
        if email_norm in existing_emails or email_norm in seen_in_payload:
            skipped += 1
            continue
        emp = CorporateProgramEmployee(
            program_id=program_id,
            full_name=row.full_name,
            email=row.email,
            phone=row.phone,
            notes=row.notes,
            enrollment_status=EmployeeEnrollmentStatus.PENDING,
        )
        db.add(emp)
        added_rows.append(emp)
        seen_in_payload.add(email_norm)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Conflict adding employees — duplicate email collision",
        )

    for emp in added_rows:
        await db.refresh(emp)

    # Keep program.employee_count in sync with the manifest.
    new_total = (
        (
            await db.execute(
                select(CorporateProgramEmployee).where(
                    CorporateProgramEmployee.program_id == program_id
                )
            )
        )
        .scalars()
        .all()
    )
    program.employee_count = len(new_total)
    await db.commit()

    return EmployeeBulkAddResponse(
        added=len(added_rows),
        skipped_duplicates=skipped,
        items=[
            CorporateProgramEmployeeResponse.model_validate(e, from_attributes=True)
            for e in added_rows
        ],
    )


@router.delete(
    "/programs/{program_id}/employees/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_employee(
    program_id: uuid.UUID,
    employee_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Remove an employee from a program's manifest.

    Does NOT undo any session bookings already created for them — admin must
    cancel those separately if needed.
    """
    emp = (
        await db.execute(
            select(CorporateProgramEmployee).where(
                CorporateProgramEmployee.id == employee_id,
                CorporateProgramEmployee.program_id == program_id,
            )
        )
    ).scalar_one_or_none()
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found on program")

    await db.delete(emp)
    # Keep employee_count in sync.
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one()
    program.employee_count = max(0, (program.employee_count or 0) - 1)
    await db.commit()
    return None


@router.post(
    "/programs/{program_id}/employees/match-members",
    response_model=MatchMembersResponse,
)
async def match_employees_to_members(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Resolve employee emails to existing member accounts.

    For every PENDING / INVITED employee row whose ``member_id`` is null,
    look up members_service by email. If a member exists, set ``member_id``
    + ``member_auth_id`` and bump status → REGISTERED.
    """
    program_exists = (
        await db.execute(
            select(CorporateProgram.id).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program_exists:
        raise HTTPException(status_code=404, detail="Program not found")

    unresolved_rows = (
        (
            await db.execute(
                select(CorporateProgramEmployee).where(
                    CorporateProgramEmployee.program_id == program_id,
                    CorporateProgramEmployee.member_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    matched = 0
    unresolved = 0
    for emp in unresolved_rows:
        record = await find_member_by_email(emp.email)
        if record is None:
            unresolved += 1
            continue
        emp.member_id = uuid.UUID(record["id"])
        emp.member_auth_id = record.get("auth_id")
        emp.enrollment_status = EmployeeEnrollmentStatus.REGISTERED
        emp.registered_at = utc_now()
        matched += 1

    already_matched = (
        (
            await db.execute(
                select(CorporateProgramEmployee).where(
                    CorporateProgramEmployee.program_id == program_id,
                    CorporateProgramEmployee.member_id.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    if matched > 0:
        await db.commit()

    return MatchMembersResponse(
        matched=matched,
        already_matched=len(already_matched) - matched,
        unresolved=unresolved,
    )
