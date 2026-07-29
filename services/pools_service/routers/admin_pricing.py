"""Admin CRUD for operating areas and effective-dated cost rates."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from services.pools_service.models import (
    OperatingArea,
    OperatingCostRate,
    Pool,
    PoolRate,
)
from services.pools_service.schemas import (
    CostQuoteRequest,
    CostQuoteResponse,
    OperatingAreaCreate,
    OperatingAreaResponse,
    OperatingAreaUpdate,
    OperatingCostRateCreate,
    OperatingCostRateResponse,
    OperatingCostRateUpdate,
    PoolRateCreate,
    PoolRateResponse,
    PoolRateUpdate,
)
from services.pools_service.services.pricing import (
    PricingAmbiguityError,
    build_cost_quote,
)

router = APIRouter(tags=["admin-pool-pricing"])


def _rate_response(rate) -> dict:
    payload = {
        column.name: getattr(rate, column.name)
        for column in rate.__table__.columns
        if column.name != "amount_kobo"
    }
    payload["amount_naira"] = (rate.amount_kobo or 0) / 100
    return payload


def _rate_create_payload(value) -> dict:
    payload = value.model_dump(exclude={"amount_naira"})
    payload["amount_kobo"] = naira_to_kobo(value.amount_naira)
    return payload


def _rate_update_payload(value) -> dict:
    payload = value.model_dump(exclude_unset=True)
    if "amount_naira" in payload:
        payload["amount_kobo"] = naira_to_kobo(payload.pop("amount_naira"))
    return payload


async def _require_pool(pool_id: uuid.UUID, db: AsyncSession) -> Pool:
    pool = (
        await db.execute(select(Pool).where(Pool.id == pool_id))
    ).scalar_one_or_none()
    if pool is None:
        raise HTTPException(status_code=404, detail="Pool not found")
    return pool


async def _require_area(area_id: uuid.UUID, db: AsyncSession) -> OperatingArea:
    area = (
        await db.execute(select(OperatingArea).where(OperatingArea.id == area_id))
    ).scalar_one_or_none()
    if area is None:
        raise HTTPException(status_code=404, detail="Operating area not found")
    return area


@router.get("/areas", response_model=list[OperatingAreaResponse])
async def list_operating_areas(
    include_inactive: bool = False,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(OperatingArea)
    if not include_inactive:
        query = query.where(OperatingArea.is_active.is_(True))
    return (
        (
            await db.execute(
                query.order_by(OperatingArea.country_code, OperatingArea.name)
            )
        )
        .scalars()
        .all()
    )


@router.post(
    "/areas",
    response_model=OperatingAreaResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operating_area(
    payload: OperatingAreaCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if payload.parent_id:
        await _require_area(payload.parent_id, db)
    duplicate = (
        await db.execute(
            select(OperatingArea.id).where(
                OperatingArea.parent_id == payload.parent_id,
                OperatingArea.slug == payload.slug,
            )
        )
    ).scalar_one_or_none()
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail="An operating area with this slug already exists at that level.",
        )
    area = OperatingArea(**payload.model_dump())
    db.add(area)
    await db.commit()
    await db.refresh(area)
    return area


@router.patch("/areas/{area_id}", response_model=OperatingAreaResponse)
async def update_operating_area(
    area_id: uuid.UUID,
    payload: OperatingAreaUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    area = await _require_area(area_id, db)
    values = payload.model_dump(exclude_unset=True)
    if values.get("parent_id") == area_id:
        raise HTTPException(status_code=400, detail="An area cannot parent itself.")
    if values.get("parent_id"):
        await _require_area(values["parent_id"], db)
    for field, value in values.items():
        setattr(area, field, value)
    await db.commit()
    await db.refresh(area)
    return area


@router.delete("/areas/{area_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_operating_area(
    area_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    area = await _require_area(area_id, db)
    area.is_active = False
    await db.commit()
    return None


@router.get("/pool-rates", response_model=list[PoolRateResponse])
async def list_pool_rates(
    pool_id: Optional[uuid.UUID] = Query(None),
    include_inactive: bool = False,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(PoolRate)
    if pool_id:
        query = query.where(PoolRate.pool_id == pool_id)
    if not include_inactive:
        query = query.where(PoolRate.is_active.is_(True))
    rows = (
        (
            await db.execute(
                query.order_by(
                    PoolRate.effective_from.desc(), PoolRate.created_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )
    return [_rate_response(row) for row in rows]


@router.post(
    "/pool-rates",
    response_model=PoolRateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pool_rate(
    payload: PoolRateCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _require_pool(payload.pool_id, db)
    rate = PoolRate(**_rate_create_payload(payload))
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return _rate_response(rate)


@router.patch("/pool-rates/{rate_id}", response_model=PoolRateResponse)
async def update_pool_rate(
    rate_id: uuid.UUID,
    payload: PoolRateUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rate = (
        await db.execute(select(PoolRate).where(PoolRate.id == rate_id))
    ).scalar_one_or_none()
    if rate is None:
        raise HTTPException(status_code=404, detail="Pool rate not found")
    for field, value in _rate_update_payload(payload).items():
        setattr(rate, field, value)
    if rate.effective_to and rate.effective_to < rate.effective_from:
        raise HTTPException(status_code=400, detail="Invalid effective date range")
    await db.commit()
    await db.refresh(rate)
    return _rate_response(rate)


@router.delete("/pool-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_pool_rate(
    rate_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rate = (
        await db.execute(select(PoolRate).where(PoolRate.id == rate_id))
    ).scalar_one_or_none()
    if rate is None:
        raise HTTPException(status_code=404, detail="Pool rate not found")
    rate.is_active = False
    await db.commit()
    return None


@router.get("/cost-rates", response_model=list[OperatingCostRateResponse])
async def list_operating_cost_rates(
    pool_id: Optional[uuid.UUID] = Query(None),
    operating_area_id: Optional[uuid.UUID] = Query(None),
    include_inactive: bool = False,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(OperatingCostRate)
    if pool_id:
        query = query.where(OperatingCostRate.pool_id == pool_id)
    if operating_area_id:
        query = query.where(OperatingCostRate.operating_area_id == operating_area_id)
    if not include_inactive:
        query = query.where(OperatingCostRate.is_active.is_(True))
    rows = (
        (
            await db.execute(
                query.order_by(
                    OperatingCostRate.category,
                    OperatingCostRate.effective_from.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [_rate_response(row) for row in rows]


@router.post(
    "/cost-rates",
    response_model=OperatingCostRateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_operating_cost_rate(
    payload: OperatingCostRateCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if payload.pool_id:
        await _require_pool(payload.pool_id, db)
    if payload.operating_area_id:
        await _require_area(payload.operating_area_id, db)
    rate = OperatingCostRate(**_rate_create_payload(payload))
    db.add(rate)
    await db.commit()
    await db.refresh(rate)
    return _rate_response(rate)


@router.patch("/cost-rates/{rate_id}", response_model=OperatingCostRateResponse)
async def update_operating_cost_rate(
    rate_id: uuid.UUID,
    payload: OperatingCostRateUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rate = (
        await db.execute(
            select(OperatingCostRate).where(OperatingCostRate.id == rate_id)
        )
    ).scalar_one_or_none()
    if rate is None:
        raise HTTPException(status_code=404, detail="Operating cost rate not found")
    values = _rate_update_payload(payload)
    next_pool_id = values.get("pool_id", rate.pool_id)
    next_area_id = values.get("operating_area_id", rate.operating_area_id)
    if next_pool_id and next_area_id:
        raise HTTPException(
            status_code=400,
            detail="Choose either an operating area or a pool, not both.",
        )
    if next_pool_id:
        await _require_pool(next_pool_id, db)
    if next_area_id:
        await _require_area(next_area_id, db)
    for field, value in values.items():
        setattr(rate, field, value)
    if rate.effective_to and rate.effective_to < rate.effective_from:
        raise HTTPException(status_code=400, detail="Invalid effective date range")
    await db.commit()
    await db.refresh(rate)
    return _rate_response(rate)


@router.delete("/cost-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_operating_cost_rate(
    rate_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rate = (
        await db.execute(
            select(OperatingCostRate).where(OperatingCostRate.id == rate_id)
        )
    ).scalar_one_or_none()
    if rate is None:
        raise HTTPException(status_code=404, detail="Operating cost rate not found")
    rate.is_active = False
    await db.commit()
    return None


@router.post("/quote", response_model=CostQuoteResponse)
async def quote_session_cost(
    payload: CostQuoteRequest,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    try:
        return await build_cost_quote(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PricingAmbiguityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
