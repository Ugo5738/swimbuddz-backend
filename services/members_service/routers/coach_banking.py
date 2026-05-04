"""Coach bank account routes.

Paystack interactions (list banks, resolve account, create transfer
recipient) go through payments-service via the `service_client` proxy
helpers — members-service does NOT import PaystackClient or hold the
PAYSTACK_SECRET_KEY. This keeps service boundaries clean and lets the
payments-service env be the sole holder of the live secret.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.common.service_client import (
    PaystackProxyError,
    paystack_create_recipient,
    paystack_list_banks,
    paystack_resolve_account,
)
from libs.db.config import AsyncSessionLocal
from services.members_service.models import CoachBankAccount, Member
from services.members_service.schemas import (
    BankAccountCreate,
    BankAccountResponse,
    BankListResponse,
    ResolveAccountRequest,
    ResolveAccountResponse,
)
from sqlalchemy import select

logger = get_logger(__name__)

router = APIRouter(prefix="/coaches", tags=["coaches"])


@router.get("/me/bank-account")
async def get_my_bank_account(
    current_user: AuthUser = Depends(get_current_user),
):
    """Get the current coach's bank account."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        # Get bank account
        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            raise HTTPException(status_code=404, detail="No bank account found")

        return BankAccountResponse(
            id=str(bank_account.id),
            member_id=str(bank_account.member_id),
            bank_code=bank_account.bank_code,
            bank_name=bank_account.bank_name,
            account_number=bank_account.account_number,
            account_name=bank_account.account_name,
            is_verified=bank_account.is_verified,
            verified_at=bank_account.verified_at,
            paystack_recipient_code=bank_account.paystack_recipient_code,
            created_at=bank_account.created_at,
            updated_at=bank_account.updated_at,
        )


@router.post("/me/bank-account")
async def create_or_update_bank_account(
    data: BankAccountCreate,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Create or update coach's bank account.

    Auto-verifies via Paystack Resolve Account API and creates
    a transfer recipient for automated payouts. Paystack calls go through
    payments-service over HTTP (`paystack_resolve_account` /
    `paystack_create_recipient`).
    """
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        # Verify account via Paystack (proxied through payments-service)
        try:
            resolved = await paystack_resolve_account(
                account_number=data.account_number,
                bank_code=data.bank_code,
                calling_service="members",
            )
            account_name = resolved["account_name"]
        except PaystackProxyError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code in (400, 502, 503) else 400,
                detail=f"Could not verify bank account: {e.message}",
            )

        # Get or create bank account record
        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            bank_account = CoachBankAccount(member_id=member.id)
            session.add(bank_account)

        # Update details
        bank_account.bank_code = data.bank_code
        bank_account.bank_name = data.bank_name
        bank_account.account_number = data.account_number
        bank_account.account_name = account_name
        bank_account.is_verified = True
        bank_account.verified_at = datetime.now(timezone.utc)
        bank_account.verified_by = "paystack_api"

        # Create Paystack transfer recipient (best-effort; can be retried later)
        try:
            recipient = await paystack_create_recipient(
                name=account_name,
                account_number=data.account_number,
                bank_code=data.bank_code,
                calling_service="members",
            )
            bank_account.paystack_recipient_code = recipient["recipient_code"]
            logger.info(
                f"Created Paystack transfer recipient for member {member.id}",
                extra={"extra_fields": {"recipient_code": recipient["recipient_code"]}},
            )
        except PaystackProxyError as e:
            # Log but don't fail - recipient can be created later
            logger.warning(
                f"Could not create Paystack transfer recipient: {e.message}",
                extra={
                    "extra_fields": {"member_id": str(member.id), "error": e.message}
                },
            )

        await session.commit()
        await session.refresh(bank_account)

        return BankAccountResponse(
            id=str(bank_account.id),
            member_id=str(bank_account.member_id),
            bank_code=bank_account.bank_code,
            bank_name=bank_account.bank_name,
            account_number=bank_account.account_number,
            account_name=bank_account.account_name,
            is_verified=bank_account.is_verified,
            verified_at=bank_account.verified_at,
            paystack_recipient_code=bank_account.paystack_recipient_code,
            created_at=bank_account.created_at,
            updated_at=bank_account.updated_at,
        )


@router.delete("/me/bank-account")
async def delete_bank_account(
    current_user: AuthUser = Depends(get_current_user),
):
    """Delete coach's bank account."""
    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        result = await session.execute(
            select(CoachBankAccount).where(CoachBankAccount.member_id == member.id)
        )
        bank_account = result.scalar_one_or_none()

        if not bank_account:
            raise HTTPException(status_code=404, detail="No bank account found")

        await session.delete(bank_account)
        await session.commit()

        return {"message": "Bank account deleted"}


@router.get("/banks")
async def list_banks():
    """
    Get list of Nigerian banks for dropdown.

    Proxied through payments-service so members-service does not need a
    PAYSTACK_SECRET_KEY. The frontend has its own hardcoded fallback list
    if this 502/503s.
    """
    try:
        banks = await paystack_list_banks(calling_service="members")
    except PaystackProxyError as e:
        logger.warning("Banks list proxy failed: %s", e.message)
        raise HTTPException(
            status_code=e.status_code if e.status_code in (502, 503) else 502,
            detail=e.message,
        )

    return [
        BankListResponse(name=b["name"], code=b["code"], slug=b.get("slug", ""))
        for b in banks
    ]


@router.post("/resolve-account")
async def resolve_bank_account(
    data: ResolveAccountRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Verify a bank account and get the account holder name.
    Free Paystack API, used for validation before saving. Proxied through
    payments-service.
    """
    try:
        resolved = await paystack_resolve_account(
            account_number=data.account_number,
            bank_code=data.bank_code,
            calling_service="members",
        )
    except PaystackProxyError as e:
        raise HTTPException(
            status_code=e.status_code if e.status_code in (400, 502, 503) else 400,
            detail=f"Could not verify account: {e.message}",
        )

    return ResolveAccountResponse(
        account_number=resolved["account_number"],
        account_name=resolved["account_name"],
        bank_code=resolved["bank_code"],
    )
