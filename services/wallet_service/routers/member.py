"""Member-facing wallet endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models import (
    TransactionType,
    WalletTopup,
    WalletTransaction,
)
from services.wallet_service.models.rewards import RewardNotificationPreference
from services.wallet_service.schemas import (
    BalanceCheckRequest,
    BalanceCheckResponse,
    CreditRequest,
    DebitRequest,
    InternalDebitCreditResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    TopupInitiateRequest,
    TopupListResponse,
    TopupResponse,
    TransactionListResponse,
    TransactionResponse,
    WalletResponse,
)
from services.wallet_service.services.topup_service import (
    get_topup,
    initiate_topup,
    reconcile_topup_return,
)
from services.wallet_service.services.wallet_ops import (
    check_balance,
    create_wallet,
    credit_wallet,
    debit_wallet,
    get_wallet_by_auth_id,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/wallet", tags=["wallet"])


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


@router.get("/me", response_model=WalletResponse)
async def get_my_wallet(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get current user's wallet (Bubble balance, status, tier)."""
    wallet = await get_wallet_by_auth_id(db, current_user.user_id)
    return wallet


@router.post(
    "/create", response_model=WalletResponse, status_code=status.HTTP_201_CREATED
)
async def create_my_wallet(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Create wallet for current user (also triggered automatically on registration)."""
    # member_id is not available from JWT alone; use user_id for both for now.
    # In production, members service calls /internal/wallet/create with both IDs.
    wallet = await create_wallet(
        db,
        member_id=uuid.UUID(current_user.user_id),
        member_auth_id=current_user.user_id,
    )
    return wallet


# ---------------------------------------------------------------------------
# Top-ups
# ---------------------------------------------------------------------------


@router.post(
    "/topup", response_model=TopupResponse, status_code=status.HTTP_201_CREATED
)
async def start_topup(
    body: TopupInitiateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Initiate Bubble purchase (returns Paystack checkout URL)."""
    topup = await initiate_topup(
        db,
        member_auth_id=current_user.user_id,
        bubbles_amount=body.bubbles_amount,
        payment_method=body.payment_method,
        payer_email=current_user.email,
        callback_url=body.callback_url,
    )
    return topup


@router.get("/topup/{topup_id}", response_model=TopupResponse)
async def get_topup_status(
    topup_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Check topup status."""
    topup = await get_topup(db, topup_id, current_user.user_id)
    return topup


@router.post("/topups/reconcile/{topup_reference}", response_model=TopupResponse)
async def reconcile_my_topup_return(
    topup_reference: str,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Reconcile topup state after Paystack redirect return."""
    topup = await reconcile_topup_return(
        db,
        topup_reference=topup_reference,
        member_auth_id=current_user.user_id,
    )
    return topup


@router.get("/topups", response_model=TopupListResponse)
async def list_my_topups(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List my topup history (paginated)."""
    wallet = await get_wallet_by_auth_id(db, current_user.user_id)

    total = (
        await db.execute(
            select(func.count())
            .select_from(WalletTopup)
            .where(WalletTopup.wallet_id == wallet.id)
        )
    ).scalar() or 0

    result = await db.execute(
        select(WalletTopup)
        .where(WalletTopup.wallet_id == wallet.id)
        .order_by(desc(WalletTopup.created_at))
        .offset(skip)
        .limit(limit)
    )
    topups = list(result.scalars().all())

    return TopupListResponse(topups=topups, total=total, skip=skip, limit=limit)


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@router.get("/transactions", response_model=TransactionListResponse)
async def list_my_transactions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    transaction_type: Optional[TransactionType] = None,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """List my transactions (paginated, filterable by type)."""
    wallet = await get_wallet_by_auth_id(db, current_user.user_id)

    base = select(WalletTransaction).where(WalletTransaction.wallet_id == wallet.id)
    count_base = (
        select(func.count())
        .select_from(WalletTransaction)
        .where(WalletTransaction.wallet_id == wallet.id)
    )
    if transaction_type:
        base = base.where(WalletTransaction.transaction_type == transaction_type)
        count_base = count_base.where(
            WalletTransaction.transaction_type == transaction_type
        )

    total = (await db.execute(count_base)).scalar() or 0
    result = await db.execute(
        base.order_by(desc(WalletTransaction.created_at)).offset(skip).limit(limit)
    )
    transactions = list(result.scalars().all())

    return TransactionListResponse(
        transactions=transactions, total=total, skip=skip, limit=limit
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
async def get_transaction_detail(
    transaction_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get transaction details."""
    wallet = await get_wallet_by_auth_id(db, current_user.user_id)
    result = await db.execute(
        select(WalletTransaction).where(
            WalletTransaction.id == transaction_id,
            WalletTransaction.wallet_id == wallet.id,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
        )
    return txn


# ---------------------------------------------------------------------------
# Spending (debit / credit / balance check)
# ---------------------------------------------------------------------------


@router.post("/debit", response_model=InternalDebitCreditResponse)
async def member_debit(
    body: DebitRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Deduct Bubbles (idempotent, requires idempotency_key)."""
    txn = await debit_wallet(
        db,
        member_auth_id=current_user.user_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
        transaction_type=body.transaction_type,
        description=body.description,
        service_source=body.service_source,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
        initiated_by=current_user.user_id,
    )
    return InternalDebitCreditResponse(
        success=True, transaction_id=txn.id, balance_after=txn.balance_after
    )


@router.post("/credit", response_model=InternalDebitCreditResponse)
async def member_credit(
    body: CreditRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Add Bubbles (refund, reward — idempotent)."""
    txn = await credit_wallet(
        db,
        member_auth_id=current_user.user_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
        transaction_type=body.transaction_type,
        description=body.description,
        service_source=body.service_source,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
        initiated_by=current_user.user_id,
    )
    return InternalDebitCreditResponse(
        success=True, transaction_id=txn.id, balance_after=txn.balance_after
    )


@router.post("/check-balance", response_model=BalanceCheckResponse)
async def member_check_balance(
    body: BalanceCheckRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Check if wallet has sufficient Bubbles (no deduction)."""
    sufficient, balance, wallet_status = await check_balance(
        db, current_user.user_id, body.required_amount
    )
    return BalanceCheckResponse(
        sufficient=sufficient,
        current_balance=balance,
        required_amount=body.required_amount,
        wallet_status=wallet_status,
    )


# ---------------------------------------------------------------------------
# Notification Preferences (Phase 3d)
# ---------------------------------------------------------------------------


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferenceResponse,
)
async def get_notification_preferences(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get reward notification preferences (lazy-creates defaults on first access)."""
    result = await db.execute(
        select(RewardNotificationPreference).where(
            RewardNotificationPreference.member_auth_id == current_user.user_id
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        pref = RewardNotificationPreference(
            member_auth_id=current_user.user_id,
        )
        db.add(pref)
        await db.flush()
        await db.commit()
        await db.refresh(pref)

    return NotificationPreferenceResponse.model_validate(pref)


@router.patch(
    "/notifications/preferences",
    response_model=NotificationPreferenceResponse,
)
async def update_notification_preferences(
    body: NotificationPreferenceUpdateRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Update reward notification preferences."""
    result = await db.execute(
        select(RewardNotificationPreference).where(
            RewardNotificationPreference.member_auth_id == current_user.user_id
        )
    )
    pref = result.scalar_one_or_none()

    if not pref:
        pref = RewardNotificationPreference(
            member_auth_id=current_user.user_id,
        )
        db.add(pref)
        await db.flush()

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pref, field, value)

    await db.flush()
    await db.commit()
    await db.refresh(pref)

    return NotificationPreferenceResponse.model_validate(pref)
