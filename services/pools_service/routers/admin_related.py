"""Admin CRUD routes for pool-related entities.

All endpoints are nested under a specific pool:
  /admin/pools/{pool_id}/contacts
  /admin/pools/{pool_id}/visits
  /admin/pools/{pool_id}/status-history  (read-only)
  /admin/pools/{pool_id}/agreements
  /admin/pools/{pool_id}/assets
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db

from services.pools_service.models import (
    Pool,
    PoolAgreement,
    PoolAsset,
    PoolContact,
    PoolStatusChange,
    PoolVisit,
)
from services.pools_service.schemas import (
    PoolAgreementCreate,
    PoolAgreementResponse,
    PoolAgreementUpdate,
    PoolAssetCreate,
    PoolAssetResponse,
    PoolAssetUpdate,
    PoolContactCreate,
    PoolContactResponse,
    PoolContactUpdate,
    PoolStatusChangeResponse,
    PoolVisitCreate,
    PoolVisitResponse,
    PoolVisitUpdate,
)

router = APIRouter(tags=["admin-pool-related"])


async def _ensure_pool_exists(pool_id: uuid.UUID, db: AsyncSession) -> Pool:
    pool = (
        await db.execute(select(Pool).where(Pool.id == pool_id))
    ).scalar_one_or_none()
    if not pool:
        raise HTTPException(status_code=404, detail="Pool not found")
    return pool


# ═════════════════════════════════════════════════════════════════════════
# CONTACTS
# ═════════════════════════════════════════════════════════════════════════


@router.get("/{pool_id}/contacts", response_model=list[PoolContactResponse])
async def list_contacts(
    pool_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    rows = (
        (
            await db.execute(
                select(PoolContact)
                .where(PoolContact.pool_id == pool_id)
                .order_by(PoolContact.is_primary.desc(), PoolContact.name.asc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post(
    "/{pool_id}/contacts",
    response_model=PoolContactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_contact(
    pool_id: uuid.UUID,
    payload: PoolContactCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)

    # If marking as primary, demote any existing primary
    if payload.is_primary:
        await _demote_other_primary_contacts(pool_id, None, db)

    contact = PoolContact(pool_id=pool_id, **payload.model_dump())
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return contact


@router.patch("/{pool_id}/contacts/{contact_id}", response_model=PoolContactResponse)
async def update_contact(
    pool_id: uuid.UUID,
    contact_id: uuid.UUID,
    payload: PoolContactUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    contact = (
        await db.execute(
            select(PoolContact).where(
                PoolContact.id == contact_id, PoolContact.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    data = payload.model_dump(exclude_unset=True)
    if data.get("is_primary") is True:
        await _demote_other_primary_contacts(pool_id, contact_id, db)
    for key, value in data.items():
        setattr(contact, key, value)

    await db.commit()
    await db.refresh(contact)
    return contact


@router.delete(
    "/{pool_id}/contacts/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_contact(
    pool_id: uuid.UUID,
    contact_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    contact = (
        await db.execute(
            select(PoolContact).where(
                PoolContact.id == contact_id, PoolContact.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    await db.delete(contact)
    await db.commit()
    return None


async def _demote_other_primary_contacts(
    pool_id: uuid.UUID, keep_id: Optional[uuid.UUID], db: AsyncSession
):
    """Ensure only one primary contact exists per pool."""
    q = select(PoolContact).where(
        PoolContact.pool_id == pool_id, PoolContact.is_primary.is_(True)
    )
    if keep_id:
        q = q.where(PoolContact.id != keep_id)
    rows = (await db.execute(q)).scalars().all()
    for row in rows:
        row.is_primary = False


# ═════════════════════════════════════════════════════════════════════════
# VISITS
# ═════════════════════════════════════════════════════════════════════════


@router.get("/{pool_id}/visits", response_model=list[PoolVisitResponse])
async def list_visits(
    pool_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    rows = (
        (
            await db.execute(
                select(PoolVisit)
                .where(PoolVisit.pool_id == pool_id)
                .order_by(PoolVisit.visit_date.desc(), PoolVisit.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post(
    "/{pool_id}/visits",
    response_model=PoolVisitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_visit(
    pool_id: uuid.UUID,
    payload: PoolVisitCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    visit = PoolVisit(
        pool_id=pool_id,
        visitor_auth_id=current_user.user_id,
        visitor_display_name=current_user.email,  # best-effort; admin can edit
        **payload.model_dump(),
    )
    db.add(visit)
    await db.commit()
    await db.refresh(visit)
    return visit


@router.patch("/{pool_id}/visits/{visit_id}", response_model=PoolVisitResponse)
async def update_visit(
    pool_id: uuid.UUID,
    visit_id: uuid.UUID,
    payload: PoolVisitUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    visit = (
        await db.execute(
            select(PoolVisit).where(
                PoolVisit.id == visit_id, PoolVisit.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(visit, key, value)
    await db.commit()
    await db.refresh(visit)
    return visit


@router.delete(
    "/{pool_id}/visits/{visit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_visit(
    pool_id: uuid.UUID,
    visit_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    visit = (
        await db.execute(
            select(PoolVisit).where(
                PoolVisit.id == visit_id, PoolVisit.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")
    await db.delete(visit)
    await db.commit()
    return None


# ═════════════════════════════════════════════════════════════════════════
# STATUS HISTORY (read-only)
# ═════════════════════════════════════════════════════════════════════════


@router.get(
    "/{pool_id}/status-history",
    response_model=list[PoolStatusChangeResponse],
)
async def list_status_history(
    pool_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    rows = (
        (
            await db.execute(
                select(PoolStatusChange)
                .where(PoolStatusChange.pool_id == pool_id)
                .order_by(PoolStatusChange.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


# ═════════════════════════════════════════════════════════════════════════
# AGREEMENTS
# ═════════════════════════════════════════════════════════════════════════


@router.get("/{pool_id}/agreements", response_model=list[PoolAgreementResponse])
async def list_agreements(
    pool_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    rows = (
        (
            await db.execute(
                select(PoolAgreement)
                .where(PoolAgreement.pool_id == pool_id)
                .order_by(PoolAgreement.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post(
    "/{pool_id}/agreements",
    response_model=PoolAgreementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agreement(
    pool_id: uuid.UUID,
    payload: PoolAgreementCreate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    agreement = PoolAgreement(pool_id=pool_id, **payload.model_dump())
    db.add(agreement)
    await db.commit()
    await db.refresh(agreement)
    return agreement


@router.patch(
    "/{pool_id}/agreements/{agreement_id}", response_model=PoolAgreementResponse
)
async def update_agreement(
    pool_id: uuid.UUID,
    agreement_id: uuid.UUID,
    payload: PoolAgreementUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    agreement = (
        await db.execute(
            select(PoolAgreement).where(
                PoolAgreement.id == agreement_id, PoolAgreement.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(agreement, key, value)
    await db.commit()
    await db.refresh(agreement)
    return agreement


@router.delete(
    "/{pool_id}/agreements/{agreement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agreement(
    pool_id: uuid.UUID,
    agreement_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    agreement = (
        await db.execute(
            select(PoolAgreement).where(
                PoolAgreement.id == agreement_id, PoolAgreement.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    await db.delete(agreement)
    await db.commit()
    return None


# ═════════════════════════════════════════════════════════════════════════
# ASSETS
# ═════════════════════════════════════════════════════════════════════════


@router.get("/{pool_id}/assets", response_model=list[PoolAssetResponse])
async def list_assets(
    pool_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    rows = (
        (
            await db.execute(
                select(PoolAsset)
                .where(PoolAsset.pool_id == pool_id)
                .order_by(
                    PoolAsset.is_primary.desc(),
                    PoolAsset.display_order.asc(),
                    PoolAsset.created_at.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return rows


@router.post(
    "/{pool_id}/assets",
    response_model=PoolAssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_asset(
    pool_id: uuid.UUID,
    payload: PoolAssetCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    await _ensure_pool_exists(pool_id, db)
    if not payload.url and not payload.media_id:
        raise HTTPException(
            status_code=400, detail="Either url or media_id must be provided"
        )
    if payload.is_primary:
        await _demote_other_primary_assets(pool_id, None, db)
    asset = PoolAsset(
        pool_id=pool_id,
        uploaded_by_auth_id=current_user.user_id,
        **payload.model_dump(),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.patch("/{pool_id}/assets/{asset_id}", response_model=PoolAssetResponse)
async def update_asset(
    pool_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: PoolAssetUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    asset = (
        await db.execute(
            select(PoolAsset).where(
                PoolAsset.id == asset_id, PoolAsset.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_primary") is True:
        await _demote_other_primary_assets(pool_id, asset_id, db)
    for key, value in data.items():
        setattr(asset, key, value)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete(
    "/{pool_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_asset(
    pool_id: uuid.UUID,
    asset_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    asset = (
        await db.execute(
            select(PoolAsset).where(
                PoolAsset.id == asset_id, PoolAsset.pool_id == pool_id
            )
        )
    ).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    await db.delete(asset)
    await db.commit()
    return None


async def _demote_other_primary_assets(
    pool_id: uuid.UUID, keep_id: Optional[uuid.UUID], db: AsyncSession
):
    q = select(PoolAsset).where(
        PoolAsset.pool_id == pool_id, PoolAsset.is_primary.is_(True)
    )
    if keep_id:
        q = q.where(PoolAsset.id != keep_id)
    rows = (await db.execute(q)).scalars().all()
    for row in rows:
        row.is_primary = False


# Helper used by admin.py to auto-log status transitions
async def record_status_change(
    pool: Pool,
    new_status,
    changed_by_auth_id: Optional[str],
    reason: Optional[str],
    db: AsyncSession,
):
    """Create a PoolStatusChange row. Called from the status-update endpoint."""
    if pool.partnership_status == new_status:
        return
    change = PoolStatusChange(
        pool_id=pool.id,
        from_status=pool.partnership_status,
        to_status=new_status,
        changed_by_auth_id=changed_by_auth_id,
        reason=reason,
        created_at=utc_now(),
    )
    db.add(change)
