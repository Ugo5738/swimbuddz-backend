"""Coach bank account routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
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
    a transfer recipient for automated payouts.
    """
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    auth_id = current_user.user_id
    if not auth_id:
        raise HTTPException(status_code=401, detail="Invalid authentication")

    async with AsyncSessionLocal() as session:
        # Get member
        result = await session.execute(select(Member).where(Member.auth_id == auth_id))
        member = result.scalar_one_or_none()

        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        # Verify account via Paystack
        paystack = PaystackClient()
        try:
            resolved = await paystack.resolve_account(
                account_number=data.account_number,
                bank_code=data.bank_code,
            )
            account_name = resolved.account_name
        except PaystackError as e:
            raise HTTPException(
                status_code=400, detail=f"Could not verify bank account: {e.message}"
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

        # Create Paystack transfer recipient
        try:
            recipient = await paystack.create_transfer_recipient(
                account_number=data.account_number,
                bank_code=data.bank_code,
                name=account_name,
            )
            bank_account.paystack_recipient_code = recipient.recipient_code
            logger.info(
                f"Created Paystack transfer recipient for member {member.id}",
                extra={"extra_fields": {"recipient_code": recipient.recipient_code}},
            )
        except PaystackError as e:
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
    Cached via Paystack API.
    """
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    paystack = PaystackClient()
    try:
        banks = await paystack.list_banks(country="nigeria")
        return [
            BankListResponse(name=b.name, code=b.code, slug=b.slug)
            for b in banks
            if b.is_active
        ]
    except PaystackError as e:
        raise HTTPException(
            status_code=500, detail=f"Could not fetch banks: {e.message}"
        )


@router.post("/resolve-account")
async def resolve_bank_account(
    data: ResolveAccountRequest,
    current_user: AuthUser = Depends(get_current_user),
):
    """
    Verify a bank account and get the account holder name.
    Free Paystack API, used for validation before saving.
    """
    from services.payments_service.services.paystack_client import (
        PaystackClient,
        PaystackError,
    )

    paystack = PaystackClient()
    try:
        resolved = await paystack.resolve_account(
            account_number=data.account_number,
            bank_code=data.bank_code,
        )
        return ResolveAccountResponse(
            account_number=resolved.account_number,
            account_name=resolved.account_name,
            bank_code=resolved.bank_code,
        )
    except PaystackError as e:
        raise HTTPException(
            status_code=400, detail=f"Could not verify account: {e.message}"
        )
