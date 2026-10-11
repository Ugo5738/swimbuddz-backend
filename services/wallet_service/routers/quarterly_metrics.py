"""Completed wallet flows, separated by direction, purpose and checkout source."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.reporting import ReportingSection, enum_value, reporting_window
from libs.db.session import get_async_db
from services.wallet_service.models import Wallet, WalletTransaction, TransactionStatus

router = APIRouter(prefix="/internal/wallet", tags=["internal"])


def summarize_transactions(rows) -> ReportingSection:
    completed = [r for r in rows if enum_value(r.status) == "completed"]
    credit = [r for r in completed if enum_value(r.direction) == "credit"]
    debit = [r for r in completed if enum_value(r.direction) == "debit"]
    rewards = [r for r in credit if enum_value(r.transaction_type) == "reward"]
    purchases = [r for r in debit if enum_value(r.transaction_type) == "purchase"]
    flows, checkout = {}, {}
    for r in completed:
        key = f"{enum_value(r.transaction_type)}_{enum_value(r.direction)}"
        bucket = flows.setdefault(key, {"transactions": 0, "bubbles": 0})
        bucket["transactions"] += 1
        bucket["bubbles"] += r.amount
    for r in purchases:
        key = r.reference_type or r.service_source or "unspecified"
        bucket = checkout.setdefault(key, {"transactions": 0, "bubbles": 0})
        bucket["transactions"] += 1
        bucket["bubbles"] += r.amount
    return ReportingSection(
        metrics={
            "wallet_credits": sum(r.amount for r in credit),
            "wallet_debits": sum(r.amount for r in debit),
            "reward_bubbles": sum(r.amount for r in rewards),
            "reward_grants": len(rewards),
            "reward_recipients": len({r.wallet_id for r in rewards}),
            "purchase_bubbles": sum(r.amount for r in purchases),
            "purchase_transactions": len(purchases),
            "purchase_users": len({r.wallet_id for r in purchases}),
            "refund_bubbles": sum(
                r.amount for r in credit if enum_value(r.transaction_type) == "refund"
            ),
            "topup_bubbles": sum(
                r.amount for r in credit if enum_value(r.transaction_type) == "topup"
            ),
        },
        details={"flows": flows, "checkout": checkout},
        notes=[
            "Completed transactions only. Credits include top-ups, refunds and adjustments; they are not all rewards. Purchase use is gross before refunds and is not accounting revenue.",
            "Checkout categories use recorded reference type/service source; unspecified entries cannot establish a product category.",
        ],
    )


@router.get("/reporting-summary", response_model=ReportingSection)
async def get_reporting_summary(
    date_from: datetime = Query(..., alias="from"),
    date_to: datetime = Query(..., alias="to"),
    member_auth_id: str | None = None,
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    start, end = reporting_window(date_from, date_to)
    query = select(WalletTransaction).where(
        WalletTransaction.created_at >= start,
        WalletTransaction.created_at < end,
        WalletTransaction.status == TransactionStatus.COMPLETED,
    )
    if member_auth_id:
        query = query.join(Wallet, Wallet.id == WalletTransaction.wallet_id).where(
            Wallet.member_auth_id == member_auth_id
        )
    return summarize_transactions((await db.execute(query)).scalars().all())
