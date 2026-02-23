"""Admin wallet management endpoints."""

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from libs.db.session import get_async_db
from services.wallet_service.models import (
    AuditAction,
    GrantType,
    TopupStatus,
    TransactionDirection,
    TransactionType,
    Wallet,
    WalletAuditLog,
    WalletStatus,
    WalletTopup,
    WalletTransaction,
)
from services.wallet_service.schemas import (
    AdjustBalanceRequest,
    AdminStatsResponse,
    AdminTopupListResponse,
    AdminTopupResponse,
    AdminTransactionListResponse,
    AdminWalletListResponse,
    AdminWalletResponse,
    AuditLogListResponse,
    FreezeWalletRequest,
    GrantListResponse,
    GrantPromotionalRequest,
    GrantResponse,
    MemberIdentityResponse,
    UnfreezeWalletRequest,
    WalletResponse,
)
from services.wallet_service.services.promotional_service import (
    grant_promotional_bubbles,
    list_grants,
)
from services.wallet_service.services.wallet_ops import (
    credit_wallet,
    debit_wallet,
    get_wallet_by_id,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter(prefix="/admin/wallet", tags=["admin-wallet"])


async def _resolve_member_identities(
    member_auth_ids: list[str],
) -> dict[str, MemberIdentityResponse]:
    """Resolve auth IDs to member identity details via members service."""
    unique_ids = [mid for mid in dict.fromkeys(member_auth_ids) if mid]
    if not unique_ids:
        return {}

    lookups = [
        get_member_by_auth_id(auth_id=auth_id, calling_service="wallet")
        for auth_id in unique_ids
    ]
    results = await asyncio.gather(*lookups, return_exceptions=True)

    identities: dict[str, MemberIdentityResponse] = {}
    for auth_id, result in zip(unique_ids, results):
        if isinstance(result, Exception):
            logger.warning(
                "Could not resolve member identity for %s: %s", auth_id, result
            )
            continue
        if not result:
            continue
        first_name = (result.get("first_name") or "").strip()
        last_name = (result.get("last_name") or "").strip()
        full_name = f"{first_name} {last_name}".strip() or None
        identities[auth_id] = MemberIdentityResponse(
            member_id=result.get("id"),
            member_auth_id=auth_id,
            first_name=first_name or None,
            last_name=last_name or None,
            full_name=full_name,
            email=result.get("email"),
        )
    return identities


# ---------------------------------------------------------------------------
# Wallet management
# ---------------------------------------------------------------------------


@router.get("/wallets", response_model=AdminWalletListResponse)
async def list_wallets(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    wallet_status: Optional[WalletStatus] = Query(None, alias="status"),
    search: Optional[str] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all wallets (paginated, filterable)."""
    query = select(Wallet)
    count_query = select(func.count()).select_from(Wallet)

    if wallet_status:
        query = query.where(Wallet.status == wallet_status)
        count_query = count_query.where(Wallet.status == wallet_status)
    if search:
        query = query.where(Wallet.member_auth_id.ilike(f"%{search}%"))
        count_query = count_query.where(Wallet.member_auth_id.ilike(f"%{search}%"))

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(desc(Wallet.created_at)).offset(skip).limit(limit)
    )
    wallets = list(result.scalars().all())
    member_map = await _resolve_member_identities([w.member_auth_id for w in wallets])

    enriched_wallets: list[AdminWalletResponse] = []
    for wallet in wallets:
        base = WalletResponse.model_validate(wallet)
        enriched_wallets.append(
            AdminWalletResponse(
                **base.model_dump(),
                member=member_map.get(wallet.member_auth_id),
            )
        )

    return AdminWalletListResponse(
        wallets=enriched_wallets, total=total, skip=skip, limit=limit
    )


@router.get("/wallets/{wallet_id}", response_model=AdminWalletResponse)
async def get_wallet_detail(
    wallet_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get wallet details."""
    wallet = await get_wallet_by_id(db, wallet_id)
    member_map = await _resolve_member_identities([wallet.member_auth_id])
    base = WalletResponse.model_validate(wallet)
    return AdminWalletResponse(
        **base.model_dump(),
        member=member_map.get(wallet.member_auth_id),
    )


@router.get("/topups", response_model=AdminTopupListResponse)
async def list_topups_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    wallet_id: Optional[uuid.UUID] = None,
    member_auth_id: Optional[str] = None,
    status_filter: Optional[TopupStatus] = Query(None, alias="status"),
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List topups with member identity enrichment for forensic tracing."""
    query = select(WalletTopup)
    count_query = select(func.count()).select_from(WalletTopup)

    if wallet_id:
        query = query.where(WalletTopup.wallet_id == wallet_id)
        count_query = count_query.where(WalletTopup.wallet_id == wallet_id)
    if member_auth_id:
        query = query.where(WalletTopup.member_auth_id == member_auth_id)
        count_query = count_query.where(WalletTopup.member_auth_id == member_auth_id)
    if status_filter:
        query = query.where(WalletTopup.status == status_filter)
        count_query = count_query.where(WalletTopup.status == status_filter)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(desc(WalletTopup.created_at)).offset(skip).limit(limit)
    )
    topups = list(result.scalars().all())
    member_map = await _resolve_member_identities([t.member_auth_id for t in topups])

    enriched_topups: list[AdminTopupResponse] = []
    for topup in topups:
        base = AdminTopupResponse.model_validate(topup)
        enriched_topups.append(
            base.model_copy(update={"member": member_map.get(topup.member_auth_id)})
        )

    return AdminTopupListResponse(
        topups=enriched_topups,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/wallets/{wallet_id}/freeze", response_model=WalletResponse)
async def freeze_wallet(
    wallet_id: uuid.UUID,
    body: FreezeWalletRequest,
    request: Request,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Freeze a wallet."""
    wallet = await get_wallet_by_id(db, wallet_id)
    if wallet.status == WalletStatus.FROZEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet is already frozen",
        )

    old_status = wallet.status.value
    wallet.status = WalletStatus.FROZEN
    wallet.frozen_reason = body.reason
    wallet.frozen_at = utc_now()
    wallet.frozen_by = admin.user_id

    audit = WalletAuditLog(
        wallet_id=wallet_id,
        action=AuditAction.FREEZE,
        performed_by=admin.user_id,
        old_value={"status": old_status},
        new_value={"status": WalletStatus.FROZEN.value, "reason": body.reason},
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)

    await db.commit()
    await db.refresh(wallet)
    logger.info("Admin %s froze wallet %s: %s", admin.user_id, wallet_id, body.reason)
    return wallet


@router.post("/wallets/{wallet_id}/unfreeze", response_model=WalletResponse)
async def unfreeze_wallet(
    wallet_id: uuid.UUID,
    body: UnfreezeWalletRequest,
    request: Request,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Unfreeze a wallet."""
    wallet = await get_wallet_by_id(db, wallet_id)
    if wallet.status != WalletStatus.FROZEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet is not frozen",
        )

    wallet.status = WalletStatus.ACTIVE
    wallet.frozen_reason = None
    wallet.frozen_at = None
    wallet.frozen_by = None

    audit = WalletAuditLog(
        wallet_id=wallet_id,
        action=AuditAction.UNFREEZE,
        performed_by=admin.user_id,
        old_value={"status": WalletStatus.FROZEN.value},
        new_value={"status": WalletStatus.ACTIVE.value},
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)

    await db.commit()
    await db.refresh(wallet)
    logger.info("Admin %s unfroze wallet %s", admin.user_id, wallet_id)
    return wallet


@router.post("/wallets/{wallet_id}/adjust", response_model=WalletResponse)
async def adjust_balance(
    wallet_id: uuid.UUID,
    body: AdjustBalanceRequest,
    request: Request,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manual Bubble credit/debit adjustment."""
    wallet = await get_wallet_by_id(db, wallet_id)
    old_balance = wallet.balance

    if body.amount > 0:
        await credit_wallet(
            db,
            member_auth_id=wallet.member_auth_id,
            amount=body.amount,
            idempotency_key=f"admin-adjust-{wallet_id}-{utc_now().timestamp():.0f}",
            transaction_type=TransactionType.ADMIN_ADJUSTMENT,
            description=f"Adjustment — credited by admin ({body.amount} 🫧)",
            service_source="wallet_service",
            initiated_by=admin.user_id,
            metadata={"reason": body.reason, "admin_id": admin.user_id},
        )
        audit_action = AuditAction.ADMIN_CREDIT
    elif body.amount < 0:
        await debit_wallet(
            db,
            member_auth_id=wallet.member_auth_id,
            amount=abs(body.amount),
            idempotency_key=f"admin-adjust-{wallet_id}-{utc_now().timestamp():.0f}",
            transaction_type=TransactionType.ADMIN_ADJUSTMENT,
            description=f"Adjustment — debited by admin ({abs(body.amount)} 🫧)",
            service_source="wallet_service",
            initiated_by=admin.user_id,
            metadata={"reason": body.reason, "admin_id": admin.user_id},
        )
        audit_action = AuditAction.ADMIN_DEBIT
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount cannot be zero",
        )

    # Re-fetch wallet after the credit/debit committed
    await db.refresh(wallet)

    audit = WalletAuditLog(
        wallet_id=wallet_id,
        action=audit_action,
        performed_by=admin.user_id,
        old_value={"balance": old_balance},
        new_value={"balance": wallet.balance},
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
    )
    db.add(audit)
    await db.commit()

    logger.info(
        "Admin %s adjusted wallet %s by %d: %s",
        admin.user_id,
        wallet_id,
        body.amount,
        body.reason,
    )
    return wallet


# ---------------------------------------------------------------------------
# Promotional grants
# ---------------------------------------------------------------------------


@router.post(
    "/grants", response_model=GrantResponse, status_code=status.HTTP_201_CREATED
)
async def create_grant(
    body: GrantPromotionalRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Issue promotional Bubbles (single member)."""
    grant = await grant_promotional_bubbles(
        db,
        member_auth_id=body.member_auth_id,
        bubbles_amount=body.bubbles_amount,
        grant_type=body.grant_type,
        reason=body.reason,
        granted_by=admin.user_id,
        campaign_code=body.campaign_code,
        expires_in_days=body.expires_in_days,
    )
    return grant


@router.get("/grants", response_model=GrantListResponse)
async def list_all_grants(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    grant_type: Optional[GrantType] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all promotional grants."""
    grants, total = await list_grants(db, grant_type=grant_type, skip=skip, limit=limit)
    return GrantListResponse(grants=grants, total=total, skip=skip, limit=limit)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStatsResponse)
async def get_stats(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """System-wide wallet statistics."""
    now = utc_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_wallets = (
        await db.execute(select(func.count()).select_from(Wallet))
    ).scalar() or 0
    active_wallets = (
        await db.execute(
            select(func.count())
            .select_from(Wallet)
            .where(Wallet.status == WalletStatus.ACTIVE)
        )
    ).scalar() or 0
    frozen_wallets = (
        await db.execute(
            select(func.count())
            .select_from(Wallet)
            .where(Wallet.status == WalletStatus.FROZEN)
        )
    ).scalar() or 0
    total_bubbles = (
        await db.execute(select(func.coalesce(func.sum(Wallet.balance), 0)))
    ).scalar() or 0

    # Spent this month
    spent_this_month = (
        await db.execute(
            select(func.coalesce(func.sum(WalletTransaction.amount), 0)).where(
                WalletTransaction.direction == TransactionDirection.DEBIT,
                WalletTransaction.created_at >= month_start,
            )
        )
    ).scalar() or 0

    # Topup revenue this month (naira)
    topup_revenue = (
        await db.execute(
            select(func.coalesce(func.sum(WalletTopup.naira_amount), 0)).where(
                WalletTopup.status == TopupStatus.COMPLETED,
                WalletTopup.completed_at >= month_start,
            )
        )
    ).scalar() or 0

    return AdminStatsResponse(
        total_wallets=total_wallets,
        active_wallets=active_wallets,
        frozen_wallets=frozen_wallets,
        total_bubbles_in_circulation=total_bubbles,
        total_bubbles_spent_this_month=spent_this_month,
        total_topup_revenue_naira_this_month=topup_revenue,
    )


# ---------------------------------------------------------------------------
# Transactions (admin view)
# ---------------------------------------------------------------------------


@router.get("/transactions", response_model=AdminTransactionListResponse)
async def list_all_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    wallet_id: Optional[uuid.UUID] = None,
    transaction_type: Optional[TransactionType] = None,
    direction: Optional[TransactionDirection] = None,
    service_source: Optional[str] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """All transactions (filterable)."""
    query = select(WalletTransaction)
    count_query = select(func.count()).select_from(WalletTransaction)

    if wallet_id:
        query = query.where(WalletTransaction.wallet_id == wallet_id)
        count_query = count_query.where(WalletTransaction.wallet_id == wallet_id)
    if transaction_type:
        query = query.where(WalletTransaction.transaction_type == transaction_type)
        count_query = count_query.where(
            WalletTransaction.transaction_type == transaction_type
        )
    if direction:
        query = query.where(WalletTransaction.direction == direction)
        count_query = count_query.where(WalletTransaction.direction == direction)
    if service_source:
        query = query.where(WalletTransaction.service_source == service_source)
        count_query = count_query.where(
            WalletTransaction.service_source == service_source
        )

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(desc(WalletTransaction.created_at)).offset(skip).limit(limit)
    )
    transactions = list(result.scalars().all())

    return AdminTransactionListResponse(
        transactions=transactions, total=total, skip=skip, limit=limit
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@router.get("/audit-log", response_model=AuditLogListResponse)
async def get_audit_log(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    wallet_id: Optional[uuid.UUID] = None,
    action: Optional[AuditAction] = None,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Admin action audit trail."""
    query = select(WalletAuditLog)
    count_query = select(func.count()).select_from(WalletAuditLog)

    if wallet_id:
        query = query.where(WalletAuditLog.wallet_id == wallet_id)
        count_query = count_query.where(WalletAuditLog.wallet_id == wallet_id)
    if action:
        query = query.where(WalletAuditLog.action == action)
        count_query = count_query.where(WalletAuditLog.action == action)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(desc(WalletAuditLog.created_at)).offset(skip).limit(limit)
    )
    entries = list(result.scalars().all())

    return AuditLogListResponse(entries=entries, total=total, skip=skip, limit=limit)
