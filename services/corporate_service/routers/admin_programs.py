"""Admin CRUD for CorporateProgram (sold cohorts)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateProgram,
    ProgramStatus,
)
from services.corporate_service.schemas import (
    CorporateProgramCreate,
    CorporateProgramListResponse,
    CorporateProgramResponse,
    CorporateProgramUpdate,
)
from services.corporate_service.services import compute_program_pricing

router = APIRouter(tags=["admin-corporate-programs"])


@router.post(
    "/programs",
    response_model=CorporateProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_program(
    payload: CorporateProgramCreate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a corporate program directly (skipping the deal pipeline).

    Used when a deal closes outside the CRM (legacy contract, partnership
    pre-dating this service, etc.). If the caller doesn't pre-compute pricing,
    we fill it in from the discount tier × employee count.
    """
    contact_exists = (
        await db.execute(
            select(CorporateContact.id).where(CorporateContact.id == payload.contact_id)
        )
    ).scalar_one_or_none()
    if not contact_exists:
        raise HTTPException(status_code=404, detail="Corporate contact not found")

    data = payload.model_dump()
    # Allow the caller to fully control pricing OR recompute from tier+count.
    if data["per_employee_kobo"] == 0 and data["total_kobo"] == 0:
        per_emp, total = compute_program_pricing(
            data["employee_count"], data["discount_tier"]
        )
        data["per_employee_kobo"] = per_emp
        data["total_kobo"] = total

    program = CorporateProgram(**data)
    db.add(program)
    await db.commit()
    await db.refresh(program)
    return program


@router.get("/programs", response_model=CorporateProgramListResponse)
async def list_programs(
    status_filter: Optional[ProgramStatus] = Query(None, alias="status"),
    contact_id: Optional[uuid.UUID] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List programs with optional status / contact filter."""
    query = select(CorporateProgram)
    if status_filter is not None:
        query = query.where(CorporateProgram.status == status_filter)
    if contact_id is not None:
        query = query.where(CorporateProgram.contact_id == contact_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(CorporateProgram.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()

    return CorporateProgramListResponse(
        items=list(items),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/programs/{program_id}", response_model=CorporateProgramResponse)
async def get_program(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    return program


@router.patch("/programs/{program_id}", response_model=CorporateProgramResponse)
async def update_program(
    program_id: uuid.UUID,
    payload: CorporateProgramUpdate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    updates = payload.model_dump(exclude_unset=True)
    pricing_inputs = {"employee_count", "discount_tier"}
    needs_recompute = (
        bool(pricing_inputs & set(updates.keys()))
        and "per_employee_kobo" not in updates
        and "total_kobo" not in updates
    )
    for field, value in updates.items():
        setattr(program, field, value)
    if needs_recompute:
        per_emp, total = compute_program_pricing(
            program.employee_count, program.discount_tier
        )
        program.per_employee_kobo = per_emp
        program.total_kobo = total

    await db.commit()
    await db.refresh(program)
    return program


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_program(
    program_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Cancel a program (soft cancel; sets status=CANCELLED).

    Does NOT call out to other services to undo bookings or close the
    wallet — that's a separate ops decision and a future endpoint.
    """
    program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.id == program_id)
        )
    ).scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")
    program.status = ProgramStatus.CANCELLED
    await db.commit()
    return None
