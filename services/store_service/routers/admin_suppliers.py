"""Admin supplier management routes."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.store_service.models import AuditEntityType, Supplier, SupplierStatus
from services.store_service.routers._helpers import log_audit
from services.store_service.schemas import (
    SupplierCreate,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)

router = APIRouter(tags=["admin-suppliers"])


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


@router.get("/suppliers", response_model=SupplierListResponse)
async def list_suppliers(
    status_filter: Optional[SupplierStatus] = None,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all suppliers with optional filters."""
    query = select(Supplier)

    if status_filter is not None:
        query = query.where(Supplier.status == status_filter)
    if is_active is not None:
        query = query.where(Supplier.is_active == is_active)
    if search:
        query = query.where(Supplier.name.ilike(f"%{search}%"))

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate
    query = query.order_by(Supplier.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    suppliers = (await db.execute(query)).scalars().all()

    return SupplierListResponse(
        items=suppliers,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@router.post(
    "/suppliers",
    response_model=SupplierResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_supplier(
    supplier_in: SupplierCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new supplier."""
    # Check slug uniqueness
    existing = await db.execute(
        select(Supplier).where(Supplier.slug == supplier_in.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=f"Supplier with slug '{supplier_in.slug}' already exists",
        )

    supplier = Supplier(**supplier_in.model_dump())
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)

    await log_audit(
        db,
        AuditEntityType.SUPPLIER,
        supplier.id,
        "created",
        current_user.user_id,
        new_value=supplier_in.model_dump(mode="json"),
    )
    await db.commit()

    return supplier


# ---------------------------------------------------------------------------
# GET DETAIL
# ---------------------------------------------------------------------------


@router.get("/suppliers/{supplier_id}", response_model=SupplierResponse)
async def get_supplier(
    supplier_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get supplier detail."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    return supplier


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@router.patch("/suppliers/{supplier_id}", response_model=SupplierResponse)
async def update_supplier(
    supplier_id: uuid.UUID,
    supplier_in: SupplierUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update a supplier."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    update_data = supplier_in.model_dump(exclude_unset=True)
    if not update_data:
        return supplier

    # Check slug uniqueness if changing
    if "slug" in update_data and update_data["slug"] != supplier.slug:
        existing = await db.execute(
            select(Supplier).where(Supplier.slug == update_data["slug"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Supplier with slug '{update_data['slug']}' already exists",
            )

    old_values = {k: getattr(supplier, k) for k in update_data}

    for field, value in update_data.items():
        setattr(supplier, field, value)

    await log_audit(
        db,
        AuditEntityType.SUPPLIER,
        supplier.id,
        "updated",
        current_user.user_id,
        old_value={k: str(v) for k, v in old_values.items()},
        new_value={k: str(v) for k, v in update_data.items()},
    )
    await db.commit()
    await db.refresh(supplier)

    return supplier


# ---------------------------------------------------------------------------
# SUSPEND
# ---------------------------------------------------------------------------


@router.post(
    "/suppliers/{supplier_id}/suspend",
    response_model=SupplierResponse,
)
async def suspend_supplier(
    supplier_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Suspend a supplier."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    if supplier.status == SupplierStatus.SUSPENDED:
        raise HTTPException(status_code=400, detail="Supplier is already suspended")

    old_status = supplier.status
    supplier.status = SupplierStatus.SUSPENDED

    await log_audit(
        db,
        AuditEntityType.SUPPLIER,
        supplier.id,
        "suspended",
        current_user.user_id,
        old_value={"status": old_status.value},
        new_value={"status": SupplierStatus.SUSPENDED.value},
    )
    await db.commit()
    await db.refresh(supplier)

    return supplier


# ---------------------------------------------------------------------------
# ACTIVATE
# ---------------------------------------------------------------------------


@router.post(
    "/suppliers/{supplier_id}/activate",
    response_model=SupplierResponse,
)
async def activate_supplier(
    supplier_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Activate (or reactivate) a supplier."""
    result = await db.execute(select(Supplier).where(Supplier.id == supplier_id))
    supplier = result.scalar_one_or_none()
    if not supplier:
        raise HTTPException(status_code=404, detail="Supplier not found")

    if supplier.status == SupplierStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Supplier is already active")

    old_status = supplier.status
    supplier.status = SupplierStatus.ACTIVE

    await log_audit(
        db,
        AuditEntityType.SUPPLIER,
        supplier.id,
        "activated",
        current_user.user_id,
        old_value={"status": old_status.value},
        new_value={"status": SupplierStatus.ACTIVE.value},
    )
    await db.commit()
    await db.refresh(supplier)

    return supplier
