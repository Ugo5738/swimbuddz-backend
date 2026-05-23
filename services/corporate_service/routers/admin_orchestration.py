"""Orchestration endpoints that call into other services.

These are the routes that take a CorporateProgram from DRAFT to fulfilled:
- link a cohort
- provision a corporate wallet
- bulk-enroll all employees with member accounts
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateProgram,
    CorporateProgramEmployee,
    EmployeeEnrollmentStatus,
    ProgramStatus,
)
from services.corporate_service.schemas import (
    CorporateProgramResponse,
    EnrollAllResponse,
    LinkCohortRequest,
    ProvisionWalletRequest,
)
from services.corporate_service.services.clients import (
    bulk_create_bookings,
    get_cohort,
    get_cohort_session_ids,
    provision_corporate_wallet,
)

router = APIRouter(tags=["admin-corporate-orchestration"])


async def _load_program(db: AsyncSession, program_id: uuid.UUID) -> CorporateProgram:
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


def _maybe_promote_to_ready(program: CorporateProgram) -> None:
    """Bump status DRAFT → READY once cohort+wallet are both linked."""
    if (
        program.status == ProgramStatus.DRAFT
        and program.cohort_id is not None
        and program.corporate_wallet_id is not None
    ):
        program.status = ProgramStatus.READY


@router.post(
    "/programs/{program_id}/link-cohort",
    response_model=CorporateProgramResponse,
)
async def link_cohort(
    program_id: uuid.UUID,
    payload: LinkCohortRequest,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Link an academy cohort to this corporate program.

    Verifies the cohort exists by calling academy_service; if so, stores the
    cohort_id on the program. (We DO NOT mutate the cohort row directly —
    cross-service writes happen via the academy admin API or a dedicated
    internal endpoint added separately.)
    """
    program = await _load_program(db, program_id)
    if program.status in (ProgramStatus.COMPLETED, ProgramStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot link cohort to a {program.status.value} program",
        )

    cohort = await get_cohort(payload.cohort_id)
    if cohort is None:
        raise HTTPException(status_code=404, detail="Cohort not found in academy")

    program.cohort_id = payload.cohort_id
    _maybe_promote_to_ready(program)
    await db.commit()
    await db.refresh(program)
    return program


@router.post(
    "/programs/{program_id}/provision-wallet",
    response_model=CorporateProgramResponse,
)
async def provision_wallet(
    program_id: uuid.UUID,
    payload: ProvisionWalletRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create the CorporateWallet for this program in wallet_service.

    Budget defaults to the program's ``total_kobo``. The wallet's
    admin_auth_id is set to the currently-logged-in admin so they own the
    wallet from day 1; can be reassigned later.
    """
    program = await _load_program(db, program_id)
    if program.corporate_wallet_id is not None:
        raise HTTPException(
            status_code=400, detail="Program already has a corporate wallet"
        )
    if program.status in (ProgramStatus.COMPLETED, ProgramStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot provision wallet for a {program.status.value} program",
        )

    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == program.contact_id)
        )
    ).scalar_one()

    budget = (
        payload.budget_kobo if payload.budget_kobo is not None else program.total_kobo
    )
    wallet = await provision_corporate_wallet(
        program_id=program.id,
        company_name=contact.company_name,
        company_email=contact.primary_contact_email,
        admin_auth_id=current_user.user_id,
        budget_kobo=budget,
        member_bubble_limit=payload.member_bubble_limit,
    )
    program.corporate_wallet_id = uuid.UUID(wallet["id"])
    _maybe_promote_to_ready(program)
    await db.commit()
    await db.refresh(program)
    return program


@router.post(
    "/programs/{program_id}/enroll-all",
    response_model=EnrollAllResponse,
)
async def enroll_all_employees(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Bulk-enroll every REGISTERED employee into every session of the cohort.

    Calls sessions_service /internal/sessions/bookings/bulk. Employees
    without a resolved ``member_id`` are skipped (run match-members first).
    The sessions endpoint is idempotent — re-running this is safe.
    """
    program = await _load_program(db, program_id)
    if program.cohort_id is None:
        raise HTTPException(
            status_code=400,
            detail="Link a cohort before enrolling employees",
        )
    if program.status in (ProgramStatus.COMPLETED, ProgramStatus.CANCELLED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot enroll employees on a {program.status.value} program",
        )

    employees = (
        (
            await db.execute(
                select(CorporateProgramEmployee).where(
                    CorporateProgramEmployee.program_id == program_id,
                )
            )
        )
        .scalars()
        .all()
    )

    enrollable = [e for e in employees if e.member_id is not None]
    skipped_no_member = len(employees) - len(enrollable)

    if not enrollable:
        return EnrollAllResponse(
            enrolled=0,
            skipped_no_member_id=skipped_no_member,
            skipped_already_booked=0,
            employee_count=len(employees),
        )

    session_ids = await get_cohort_session_ids(program.cohort_id)
    if not session_ids:
        raise HTTPException(
            status_code=400,
            detail="Cohort has no sessions scheduled yet",
        )

    items = []
    for emp in enrollable:
        for session_id in session_ids:
            items.append(
                {
                    "session_id": session_id,
                    "member_id": str(emp.member_id),
                    "member_auth_id": emp.member_auth_id,
                    # Corporate covers the full session fee — wallet
                    # settlement is a separate concern. Pass 0 kobo here so
                    # the booking row records "sponsor paid 0 from member"
                    # rather than a misleading per-session amount.
                    "fee_amount_kobo": 0,
                }
            )

    response = await bulk_create_bookings(
        corporate_program_id=program.id,
        items=items,
    )

    # Mark enrollable employees as ENROLLED.
    now = utc_now()
    for emp in enrollable:
        if emp.enrollment_status != EmployeeEnrollmentStatus.ENROLLED:
            emp.enrollment_status = EmployeeEnrollmentStatus.ENROLLED
            emp.enrolled_at = now

    # Bump program to ACTIVE if we just enrolled anyone and the program was READY.
    if program.status == ProgramStatus.READY and response.get("created", 0) > 0:
        program.status = ProgramStatus.ACTIVE
        if program.actual_start_date is None:
            program.actual_start_date = now.date()

    await db.commit()

    return EnrollAllResponse(
        enrolled=response.get("created", 0),
        skipped_no_member_id=skipped_no_member,
        skipped_already_booked=response.get("skipped", 0),
        employee_count=len(employees),
    )
