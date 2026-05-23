"""Admin CRUD for CorporateContact (sales accounts)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CompanyIndustry,
    CompanySize,
    ContactSource,
    CorporateContact,
)
from services.corporate_service.schemas import (
    CorporateContactCreate,
    CorporateContactListResponse,
    CorporateContactResponse,
    CorporateContactUpdate,
)

router = APIRouter(tags=["admin-corporate-contacts"])


@router.get("/contacts", response_model=CorporateContactListResponse)
async def list_contacts(
    industry: Optional[CompanyIndustry] = None,
    company_size: Optional[CompanySize] = None,
    source: Optional[ContactSource] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = Query(
        None, description="Match company_name or contact email/name"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List corporate contacts with filters."""
    query = select(CorporateContact)

    if industry is not None:
        query = query.where(CorporateContact.industry == industry)
    if company_size is not None:
        query = query.where(CorporateContact.company_size == company_size)
    if source is not None:
        query = query.where(CorporateContact.source == source)
    if is_active is not None:
        query = query.where(CorporateContact.is_active == is_active)
    if search:
        term = f"%{search}%"
        query = query.where(
            or_(
                CorporateContact.company_name.ilike(term),
                CorporateContact.primary_contact_name.ilike(term),
                CorporateContact.primary_contact_email.ilike(term),
            )
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(CorporateContact.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()

    return CorporateContactListResponse(
        items=list(items),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/contacts",
    response_model=CorporateContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    payload: CorporateContactCreate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new corporate contact."""
    contact = CorporateContact(**payload.model_dump())
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.get("/contacts/{contact_id}", response_model=CorporateContactResponse)
async def get_contact(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a corporate contact by id."""
    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Corporate contact not found")
    return contact


@router.patch("/contacts/{contact_id}", response_model=CorporateContactResponse)
async def update_contact(
    contact_id: uuid.UUID,
    payload: CorporateContactUpdate,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Patch fields on a corporate contact."""
    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Corporate contact not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(contact, field, value)

    await db.commit()
    await db.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Soft-delete a corporate contact (is_active=False)."""
    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Corporate contact not found")
    contact.is_active = False
    await db.commit()
    return None
