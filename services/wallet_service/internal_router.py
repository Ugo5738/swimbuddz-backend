"""Internal service-to-service wallet endpoints.

These endpoints are called by other SwimBuddz services via service-role JWT,
not by frontend clients directly.
"""

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.wallet_service.schemas import (
    BalanceCheckRequest,
    BalanceCheckResponse,
    BalanceResponse,
    ConfirmTopupRequest,
    CreditRequest,
    DebitRequest,
    GrantWelcomeBonusRequest,
    GrantWelcomeBonusResponse,
    InternalDebitCreditResponse,
    WalletCreateRequest,
    WalletResponse,
)
from services.wallet_service.services.topup_service import confirm_topup
from services.wallet_service.services.wallet_ops import (
    WELCOME_BONUS_BUBBLES,
    check_balance,
    create_wallet,
    credit_wallet,
    debit_wallet,
    grant_welcome_bonus_if_eligible,
    get_wallet_by_auth_id,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)
router = APIRouter(prefix="/internal/wallet", tags=["internal-wallet"])


@router.post("/debit", response_model=InternalDebitCreditResponse)
async def internal_debit(
    body: DebitRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Deduct Bubbles for a service purchase."""
    txn = await debit_wallet(
        db,
        member_auth_id=body.member_auth_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
        transaction_type=body.transaction_type,
        description=body.description,
        service_source=body.service_source,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
    )
    return InternalDebitCreditResponse(
        success=True, transaction_id=txn.id, balance_after=txn.balance_after
    )


@router.post("/credit", response_model=InternalDebitCreditResponse)
async def internal_credit(
    body: CreditRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Credit Bubbles (refund, reward)."""
    txn = await credit_wallet(
        db,
        member_auth_id=body.member_auth_id,
        amount=body.amount,
        idempotency_key=body.idempotency_key,
        transaction_type=body.transaction_type,
        description=body.description,
        service_source=body.service_source,
        reference_type=body.reference_type,
        reference_id=body.reference_id,
    )
    return InternalDebitCreditResponse(
        success=True, transaction_id=txn.id, balance_after=txn.balance_after
    )


@router.get("/balance/{auth_id}", response_model=BalanceResponse)
async def internal_get_balance(
    auth_id: str,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Check member's Bubble balance."""
    wallet = await get_wallet_by_auth_id(db, auth_id)
    return BalanceResponse(
        wallet_id=wallet.id,
        member_auth_id=wallet.member_auth_id,
        balance=wallet.balance,
        status=wallet.status,
    )


@router.post("/check-balance", response_model=BalanceCheckResponse)
async def internal_check_balance(
    body: BalanceCheckRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Verify sufficient Bubbles without deducting."""
    sufficient, balance, wallet_status = await check_balance(
        db, body.member_auth_id, body.required_amount
    )
    return BalanceCheckResponse(
        sufficient=sufficient,
        current_balance=balance,
        required_amount=body.required_amount,
        wallet_status=wallet_status,
    )


@router.post("/confirm-topup")
async def internal_confirm_topup(
    body: ConfirmTopupRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Called by payments_service on Paystack webhook to confirm topup."""
    topup = await confirm_topup(
        db,
        topup_reference=body.topup_reference,
        payment_reference=body.payment_reference,
        payment_status=body.payment_status,
    )
    return {
        "success": True,
        "topup_id": str(topup.id),
        "bubbles_credited": topup.bubbles_amount,
        "status": topup.status.value,
    }


@router.post("/create", response_model=WalletResponse)
async def internal_create_wallet(
    body: WalletCreateRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Called by members_service on member registration to create wallet."""
    wallet = await create_wallet(
        db,
        member_id=body.member_id,
        member_auth_id=str(body.member_auth_id),
    )
    return wallet


@router.post("/welcome-bonus", response_model=GrantWelcomeBonusResponse)
async def internal_grant_welcome_bonus(
    body: GrantWelcomeBonusRequest,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply one-time welcome bonus after paid activation if eligible."""
    wallet, granted = await grant_welcome_bonus_if_eligible(
        db,
        member_id=body.member_id,
        member_auth_id=str(body.member_auth_id),
        eligible=body.eligible,
        granted_by=body.granted_by or "system",
        reason=body.reason,
    )
    return GrantWelcomeBonusResponse(
        success=True,
        wallet_id=wallet.id,
        bonus_granted=granted,
        bubbles_awarded=WELCOME_BONUS_BUBBLES if granted else 0,
    )
