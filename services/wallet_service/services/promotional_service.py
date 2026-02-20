"""Promotional Bubble grants — admin and system-issued bonuses."""

import uuid
from datetime import timedelta
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from services.wallet_service.models import (
    GrantType,
    PromotionalBubbleGrant,
    TransactionType,
)
from services.wallet_service.services.wallet_ops import (
    credit_wallet,
    get_wallet_by_auth_id,
)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Default expiry for promotional Bubbles (design doc Section 8)
DEFAULT_PROMO_EXPIRY_DAYS = 60


async def grant_promotional_bubbles(
    db: AsyncSession,
    *,
    member_auth_id: str,
    bubbles_amount: int,
    grant_type: GrantType,
    reason: str,
    granted_by: str,
    campaign_code: Optional[str] = None,
    expires_in_days: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> PromotionalBubbleGrant:
    """Issue promotional Bubbles to a member.

    Creates a PromotionalBubbleGrant record and credits the wallet.
    """
    wallet = await get_wallet_by_auth_id(db, member_auth_id)

    # Calculate expiry
    # Scholarship and discount credits never expire — they're real fee reductions.
    # Welcome bonus never expires either. Everything else gets a default 60-day window.
    _no_expiry_types = (
        GrantType.WELCOME_BONUS,
        GrantType.SCHOLARSHIP,
        GrantType.DISCOUNT,
    )
    expires_at = None
    if expires_in_days is not None:
        expires_at = utc_now() + timedelta(days=expires_in_days)
    elif grant_type not in _no_expiry_types:
        # Default expiry for non-welcome, non-scholarship grants
        expires_at = utc_now() + timedelta(days=DEFAULT_PROMO_EXPIRY_DAYS)

    grant = PromotionalBubbleGrant(
        wallet_id=wallet.id,
        member_auth_id=member_auth_id,
        grant_type=grant_type,
        bubbles_amount=bubbles_amount,
        bubbles_remaining=bubbles_amount,
        reason=reason,
        campaign_code=campaign_code,
        expires_at=expires_at,
        granted_by=granted_by,
        grant_metadata=metadata,
    )
    db.add(grant)
    await db.flush()

    # Determine description from grant type
    if grant_type == GrantType.CAMPAIGN and campaign_code:
        desc_text = f"Promo — {campaign_code} ({bubbles_amount} 🫧)"
    elif grant_type == GrantType.COMPENSATION:
        desc_text = f"Adjustment — credited by admin ({bubbles_amount} 🫧)"
    elif grant_type == GrantType.SCHOLARSHIP:
        desc_text = f"Scholarship credit — {reason} ({bubbles_amount} 🫧)"
    elif grant_type == GrantType.DISCOUNT:
        desc_text = f"Discount credit — {reason} ({bubbles_amount} 🫧)"
    else:
        desc_text = f"Promo — {reason} ({bubbles_amount} 🫧)"

    txn = await credit_wallet(
        db,
        member_auth_id=member_auth_id,
        amount=bubbles_amount,
        idempotency_key=f"grant-{grant.id}",
        transaction_type=TransactionType.PROMOTIONAL_CREDIT,
        description=desc_text,
        service_source="wallet_service",
        reference_type="grant",
        reference_id=str(grant.id),
        initiated_by=granted_by,
        metadata=metadata,
    )

    grant.transaction_id = txn.id
    await db.commit()
    await db.refresh(grant)

    logger.info(
        "Granted %d promo bubbles to %s (grant %s, type=%s)",
        bubbles_amount,
        member_auth_id,
        grant.id,
        grant_type.value,
    )
    return grant


async def list_grants(
    db: AsyncSession,
    *,
    wallet_id: Optional[uuid.UUID] = None,
    grant_type: Optional[GrantType] = None,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[PromotionalBubbleGrant], int]:
    """List promotional grants with optional filters."""
    query = select(PromotionalBubbleGrant)
    count_query = select(func.count()).select_from(PromotionalBubbleGrant)

    if wallet_id:
        query = query.where(PromotionalBubbleGrant.wallet_id == wallet_id)
        count_query = count_query.where(PromotionalBubbleGrant.wallet_id == wallet_id)
    if grant_type:
        query = query.where(PromotionalBubbleGrant.grant_type == grant_type)
        count_query = count_query.where(PromotionalBubbleGrant.grant_type == grant_type)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(desc(PromotionalBubbleGrant.created_at))
        .offset(skip)
        .limit(limit)
    )
    grants = list(result.scalars().all())
    return grants, total
