"""Internal service-to-service wallet endpoints.

These endpoints are called by other SwimBuddz services via service-role JWT,
not by frontend clients directly.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.wallet_service.schemas import (
    AdminScholarshipCreditRequest,
    BalanceCheckRequest,
    BalanceCheckResponse,
    BalanceResponse,
    ConfirmTopupRequest,
    CreditRequest,
    DebitRequest,
    GrantResponse,
    GrantWelcomeBonusRequest,
    GrantWelcomeBonusResponse,
    InternalDebitCreditResponse,
    WalletCreateRequest,
    WalletResponse,
)
from services.wallet_service.services.promotional_service import (
    grant_promotional_bubbles,
)
from services.wallet_service.services.referral_service import (
    apply_referral_code,
    check_and_qualify_referral,
)
from services.wallet_service.services.topup_service import confirm_topup
from services.wallet_service.services.wallet_ops import (
    WELCOME_BONUS_BUBBLES,
    check_balance,
    create_wallet,
    credit_wallet,
    debit_wallet,
    get_wallet_by_auth_id,
    grant_welcome_bonus_if_eligible,
)

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

    # Apply referral code if provided (best-effort; don't fail wallet creation)
    if body.referral_code:
        try:
            await apply_referral_code(str(body.member_auth_id), body.referral_code, db)
            logger.info(
                "Applied referral code %s for new member %s",
                body.referral_code,
                body.member_auth_id,
            )
        except (ValueError, Exception) as e:
            logger.warning(
                "Failed to apply referral code %s for %s: %s",
                body.referral_code,
                body.member_auth_id,
                e,
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


@router.post(
    "/scholarship-credit",
    response_model=GrantResponse,
    status_code=201,
)
async def internal_scholarship_credit(
    body: AdminScholarshipCreditRequest,
    caller: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Credit Bubbles to a student's wallet as a scholarship or discount.

    Called by academy_service admin endpoints or admin tooling to deposit Bubbles
    that cover part or all of a student's installment obligation.

    The credit is recorded as a promotional grant (scholarship/discount type) and
    uses ``body.idempotency_key`` to prevent duplicate credits on retry.

    Auth: service-role JWT only.
    """
    grant = await grant_promotional_bubbles(
        db,
        member_auth_id=body.member_auth_id,
        bubbles_amount=body.amount,
        grant_type=body.grant_type,
        reason=body.reason,
        campaign_code=body.enrollment_id,  # enrollment_id used as audit trail code
        granted_by=caller.user_id or "service",
    )
    logger.info(
        "Scholarship/discount credit of %d Bubbles granted to %s (enrollment=%s)",
        body.amount,
        body.member_auth_id,
        body.enrollment_id,
    )
    return grant


@router.post("/referral-qualify")
async def internal_referral_qualify(
    body: dict,
    _service: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Check and qualify a referral after a qualifying event (e.g. membership payment).

    Called by payments_service after successful membership payment.
    Body: { "member_auth_id": str, "trigger": str }
    """
    member_auth_id = body.get("member_auth_id")
    trigger = body.get("trigger", "membership_payment")

    if not member_auth_id:
        return {"success": False, "message": "member_auth_id is required"}

    try:
        record = await check_and_qualify_referral(member_auth_id, trigger, db)
        if record:
            logger.info(
                "Referral qualified for %s via %s (status=%s)",
                member_auth_id,
                trigger,
                record.status.value,
            )
            return {
                "success": True,
                "qualified": True,
                "status": record.status.value,
            }
        return {
            "success": True,
            "qualified": False,
            "message": "No pending referral found",
        }
    except Exception as e:
        logger.warning("Referral qualification failed for %s: %s", member_auth_id, e)
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Reporting: member wallet summary
# ---------------------------------------------------------------------------


class MemberWalletSummary(BaseModel):
    bubbles_earned: int = 0
    bubbles_spent: int = 0


@router.get(
    "/wallet/member-summary/{member_auth_id}",
    response_model=MemberWalletSummary,
)
async def get_member_wallet_summary(
    member_auth_id: str,
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Aggregate wallet stats for a member within a date range.

    Used by the reporting service for quarterly reports.
    """
    from services.wallet_service.models import (
        TransactionDirection,
        Wallet,
        WalletTransaction,
    )

    # Find wallet for this member
    wallet_result = await db.execute(
        select(Wallet.id).where(Wallet.member_auth_id == member_auth_id)
    )
    wallet_id = wallet_result.scalar_one_or_none()
    if wallet_id is None:
        return MemberWalletSummary()

    # Aggregate credits (earned) and debits (spent)
    result = await db.execute(
        select(
            func.coalesce(
                func.sum(
                    func.case(
                        (
                            WalletTransaction.direction == TransactionDirection.CREDIT,
                            WalletTransaction.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("earned"),
            func.coalesce(
                func.sum(
                    func.case(
                        (
                            WalletTransaction.direction == TransactionDirection.DEBIT,
                            WalletTransaction.amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("spent"),
        ).where(
            WalletTransaction.wallet_id == wallet_id,
            WalletTransaction.created_at >= date_from,
            WalletTransaction.created_at <= date_to,
        )
    )
    row = result.one()

    return MemberWalletSummary(
        bubbles_earned=int(row.earned or 0),
        bubbles_spent=int(row.spent or 0),
    )
