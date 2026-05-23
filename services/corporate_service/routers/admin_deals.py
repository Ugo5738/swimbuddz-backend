"""Admin CRUD for CorporateDeal (sales pipeline)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateDeal,
    CorporateProgram,
    DealStage,
    PaymentTerms,
    ProgramStatus,
)
from services.corporate_service.schemas import (
    CorporateDealCreate,
    CorporateDealListResponse,
    CorporateDealLossRequest,
    CorporateDealResponse,
    CorporateDealUpdate,
    CorporateDealWinRequest,
    CorporateProgramResponse,
)
from services.corporate_service.services import compute_program_pricing

router = APIRouter(tags=["admin-corporate-deals"])


# ---------------------------------------------------------------------------
# Pipeline view & create (deal lives under contact)
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/deals",
    response_model=CorporateDealResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_deal(
    contact_id: uuid.UUID,
    payload: CorporateDealCreate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Open a new deal under an existing contact."""
    contact_exists = (
        await db.execute(
            select(CorporateContact.id).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact_exists:
        raise HTTPException(status_code=404, detail="Corporate contact not found")

    deal = CorporateDeal(contact_id=contact_id, **payload.model_dump())
    db.add(deal)
    await db.commit()
    await db.refresh(deal)
    return deal


@router.get("/deals", response_model=CorporateDealListResponse)
async def list_deals(
    stage: Optional[DealStage] = None,
    contact_id: Optional[uuid.UUID] = None,
    owner_auth_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Pipeline view — list deals across all contacts."""
    query = select(CorporateDeal)
    if stage is not None:
        query = query.where(CorporateDeal.stage == stage)
    if contact_id is not None:
        query = query.where(CorporateDeal.contact_id == contact_id)
    if owner_auth_id is not None:
        query = query.where(CorporateDeal.owner_auth_id == owner_auth_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(CorporateDeal.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()

    return CorporateDealListResponse(
        items=list(items),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/deals/{deal_id}", response_model=CorporateDealResponse)
async def get_deal(
    deal_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    deal = (
        await db.execute(select(CorporateDeal).where(CorporateDeal.id == deal_id))
    ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    return deal


@router.patch("/deals/{deal_id}", response_model=CorporateDealResponse)
async def update_deal(
    deal_id: uuid.UUID,
    payload: CorporateDealUpdate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    deal = (
        await db.execute(select(CorporateDeal).where(CorporateDeal.id == deal_id))
    ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    updates = payload.model_dump(exclude_unset=True)
    # Forbid manual transition to WON/LOST via PATCH — use dedicated /win and /lose.
    if updates.get("stage") in (DealStage.WON, DealStage.LOST):
        raise HTTPException(
            status_code=400,
            detail="Use POST /deals/{id}/win or /deals/{id}/lose to close a deal",
        )
    for field, value in updates.items():
        setattr(deal, field, value)
    await db.commit()
    await db.refresh(deal)
    return deal


# ---------------------------------------------------------------------------
# Pipeline transitions: WIN (creates a CorporateProgram) and LOSE
# ---------------------------------------------------------------------------


@router.post(
    "/deals/{deal_id}/win",
    response_model=CorporateProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def win_deal(
    deal_id: uuid.UUID,
    payload: CorporateDealWinRequest,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark a deal as won and create the corresponding CorporateProgram (DRAFT).

    The resulting program is the orchestration root for everything that follows:
    employee manifest, cohort linking, wallet provisioning, bulk enrollment.
    """
    deal = (
        await db.execute(select(CorporateDeal).where(CorporateDeal.id == deal_id))
    ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    if deal.stage in (DealStage.WON, DealStage.LOST):
        raise HTTPException(
            status_code=400, detail=f"Deal is already closed ({deal.stage.value})"
        )

    existing_program = (
        await db.execute(
            select(CorporateProgram).where(CorporateProgram.deal_id == deal_id)
        )
    ).scalar_one_or_none()
    if existing_program:
        raise HTTPException(
            status_code=400, detail="A program already exists for this deal"
        )

    per_emp, total = compute_program_pricing(
        payload.employee_count, payload.discount_tier
    )

    payment_terms = (
        PaymentTerms(payload.payment_terms)
        if payload.payment_terms
        else PaymentTerms.DEPOSIT_HALF
    )

    program = CorporateProgram(
        contact_id=deal.contact_id,
        deal_id=deal.id,
        name=payload.program_name,
        status=ProgramStatus.DRAFT,
        employee_count=payload.employee_count,
        discount_tier=payload.discount_tier,
        per_employee_kobo=per_emp,
        total_kobo=total,
        payment_terms=payment_terms,
        is_pilot_partner=payload.is_pilot_partner,
        expected_start_date=payload.expected_start_date,
        expected_end_date=payload.expected_end_date,
        notes=payload.notes,
    )
    db.add(program)

    # Close the deal.
    deal.stage = DealStage.WON
    deal.actual_close_date = utc_now().date()

    await db.commit()
    await db.refresh(program)
    return program


@router.post("/deals/{deal_id}/lose", response_model=CorporateDealResponse)
async def lose_deal(
    deal_id: uuid.UUID,
    payload: CorporateDealLossRequest,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Close a deal as lost with a reason."""
    deal = (
        await db.execute(select(CorporateDeal).where(CorporateDeal.id == deal_id))
    ).scalar_one_or_none()
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    if deal.stage in (DealStage.WON, DealStage.LOST):
        raise HTTPException(
            status_code=400, detail=f"Deal is already closed ({deal.stage.value})"
        )

    deal.stage = DealStage.LOST
    deal.lost_reason = payload.lost_reason
    deal.lost_notes = payload.lost_notes
    deal.actual_close_date = utc_now().date()
    await db.commit()
    await db.refresh(deal)
    return deal
