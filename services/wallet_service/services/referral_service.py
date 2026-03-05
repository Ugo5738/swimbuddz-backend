"""Core referral service — code management, application, qualification, and reward distribution.

Reward amounts are configured via RewardRule records in the database
(event_types: referral.qualified, referral.milestone) and processed
through the rewards engine — NOT hardcoded here.
"""

import uuid as _uuid
from datetime import timedelta
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_member_by_auth_id
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import ReferralStatus
from services.wallet_service.models.referral import ReferralCode, ReferralRecord
from services.wallet_service.models.rewards import WalletEvent
from services.wallet_service.services.code_generator import generate_referral_code
from services.wallet_service.services.rewards_engine import process_event

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AMBASSADOR_THRESHOLD = 10  # successful referrals to reach ambassador
MAX_REFERRALS_PER_CODE = 50
CODE_EXPIRY_DAYS = 90
MIN_TOPUP_FOR_QUALIFICATION = 25  # Bubbles


# ---------------------------------------------------------------------------
# Get or create referral code
# ---------------------------------------------------------------------------
async def get_or_create_referral_code(auth_id: str, db: AsyncSession) -> ReferralCode:
    """Get existing active referral code or create a new one.

    Handles the race condition where concurrent requests both try to create a
    code at the same time.  If the INSERT hits a UniqueViolation on the
    ``ix_referral_codes_member_auth_id`` index we roll back and re-fetch.
    """
    result = await db.execute(
        select(ReferralCode).where(
            ReferralCode.member_auth_id == auth_id,
            ReferralCode.is_active.is_(True),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    # Fetch member name for code generation
    member = await get_member_by_auth_id(auth_id, calling_service="wallet")
    first_name = (member or {}).get("first_name", "MEMBER")

    code = await generate_referral_code(first_name, db)

    referral_code = ReferralCode(
        member_auth_id=auth_id,
        code=code,
        is_active=True,
        max_uses=MAX_REFERRALS_PER_CODE,
        uses_count=0,
        successful_referrals=0,
        expires_at=utc_now() + timedelta(days=CODE_EXPIRY_DAYS),
    )
    db.add(referral_code)

    try:
        await db.commit()
    except IntegrityError:
        # Another concurrent request already inserted a row — roll back and
        # re-fetch the winner.
        await db.rollback()
        logger.debug(
            "Race condition on referral code creation for %s — re-fetching.", auth_id
        )
        retry_result = await db.execute(
            select(ReferralCode).where(
                ReferralCode.member_auth_id == auth_id,
                ReferralCode.is_active.is_(True),
            )
        )
        existing = retry_result.scalar_one_or_none()
        if existing:
            return existing
        # Extremely unlikely: the winner row vanished between rollback and
        # re-select.  Re-raise so the caller sees a clear error.
        raise

    await db.refresh(referral_code)
    logger.info("Created referral code %s for %s", code, auth_id)
    return referral_code


# ---------------------------------------------------------------------------
# Apply referral code (during registration)
# ---------------------------------------------------------------------------
async def apply_referral_code(
    referee_auth_id: str, code: str, db: AsyncSession
) -> ReferralRecord:
    """Apply a referral code for a new member.

    Validates the code and creates a ReferralRecord with status=REGISTERED.
    Called during/after member registration.
    """
    now = utc_now()

    # 1. Find the referral code
    result = await db.execute(
        select(ReferralCode).where(ReferralCode.code == code.upper().strip())
    )
    referral_code = result.scalar_one_or_none()

    if not referral_code:
        raise ValueError("Invalid referral code.")

    if not referral_code.is_active:
        raise ValueError("This referral code is no longer active.")

    if referral_code.expires_at and referral_code.expires_at < now:
        raise ValueError("This referral code has expired.")

    if referral_code.max_uses and referral_code.uses_count >= referral_code.max_uses:
        raise ValueError("This referral code has reached its maximum uses.")

    # 2. Self-referral check
    if referral_code.member_auth_id == referee_auth_id:
        raise ValueError("You cannot use your own referral code.")

    # 3. Duplicate check — referee already has a referral record
    existing = await db.execute(
        select(ReferralRecord.id).where(
            ReferralRecord.referee_auth_id == referee_auth_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError("A referral has already been applied for this account.")

    # 4. Create record
    record = ReferralRecord(
        referrer_auth_id=referral_code.member_auth_id,
        referee_auth_id=referee_auth_id,
        referral_code_id=referral_code.id,
        referral_code=referral_code.code,
        status=ReferralStatus.REGISTERED,
        referee_registered_at=now,
    )
    db.add(record)

    # 5. Update code stats
    referral_code.uses_count += 1
    referral_code.last_used_at = now
    # Refresh expiry on usage
    referral_code.expires_at = now + timedelta(days=CODE_EXPIRY_DAYS)

    await db.commit()
    await db.refresh(record)

    logger.info(
        "Referral code %s applied by %s (referrer=%s)",
        code,
        referee_auth_id,
        referral_code.member_auth_id,
    )
    return record


# ---------------------------------------------------------------------------
# Qualification check + reward distribution
# ---------------------------------------------------------------------------
async def check_and_qualify_referral(
    referee_auth_id: str, trigger: str, db: AsyncSession
) -> Optional[ReferralRecord]:
    """Check if a referred member qualifies and distribute rewards.

    Called after a qualifying event (first topup >= 25 Bubbles or membership payment).
    Returns the updated record, or None if no pending referral found.
    """
    result = await db.execute(
        select(ReferralRecord).where(
            ReferralRecord.referee_auth_id == referee_auth_id,
            ReferralRecord.status == ReferralStatus.REGISTERED,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None

    now = utc_now()
    record.status = ReferralStatus.QUALIFIED
    record.qualified_at = now
    record.qualification_trigger = trigger
    await db.flush()

    # Distribute rewards
    await _distribute_referral_rewards(record, db)

    await db.commit()
    await db.refresh(record)

    logger.info(
        "Referral %s qualified via %s — rewards distributed (referrer=%s, referee=%s)",
        record.id,
        trigger,
        record.referrer_auth_id,
        record.referee_auth_id,
    )
    return record


async def _distribute_referral_rewards(
    record: ReferralRecord, db: AsyncSession
) -> None:
    """Emit reward events for referrer and referee through the rewards engine.

    Amounts are determined by active RewardRule records for event_type
    ``referral.qualified`` (with ``target`` condition) and ``referral.milestone``.
    """
    now = utc_now()

    # Fetch names for event_data (used in reward description templates)
    referrer_member = await get_member_by_auth_id(
        record.referrer_auth_id, calling_service="wallet"
    )
    referee_member = await get_member_by_auth_id(
        record.referee_auth_id, calling_service="wallet"
    )
    referrer_name = (referrer_member or {}).get("first_name", "A friend")
    referee_name = (referee_member or {}).get("first_name", "A friend")

    # ------------------------------------------------------------------
    # 1. Emit referral.qualified event for the REFERRER
    # ------------------------------------------------------------------
    referrer_event = WalletEvent(
        event_id=_uuid.uuid4(),
        event_type="referral.qualified",
        member_auth_id=record.referrer_auth_id,
        service_source="wallet",
        occurred_at=now,
        event_data={
            "target": "referrer",
            "referee_name": referee_name,
            "referee_auth_id": record.referee_auth_id,
            "referral_code": record.referral_code,
        },
        idempotency_key=f"referral-referrer-{record.id}",
    )
    db.add(referrer_event)
    await db.flush()

    referrer_grants = await process_event(referrer_event, db)
    referrer_bubbles = sum(g["bubbles"] for g in referrer_grants)
    record.referrer_reward_bubbles = referrer_bubbles or None
    if referrer_grants:
        record.referrer_transaction_id = referrer_grants[0].get("transaction_id")

    # ------------------------------------------------------------------
    # 2. Emit referral.qualified event for the REFEREE
    # ------------------------------------------------------------------
    referee_event = WalletEvent(
        event_id=_uuid.uuid4(),
        event_type="referral.qualified",
        member_auth_id=record.referee_auth_id,
        service_source="wallet",
        occurred_at=now,
        event_data={
            "target": "referee",
            "referrer_name": referrer_name,
            "referrer_auth_id": record.referrer_auth_id,
            "referral_code": record.referral_code,
        },
        idempotency_key=f"referral-referee-{record.id}",
    )
    db.add(referee_event)
    await db.flush()

    referee_grants = await process_event(referee_event, db)
    referee_bubbles = sum(g["bubbles"] for g in referee_grants)
    record.referee_reward_bubbles = referee_bubbles or None
    if referee_grants:
        record.referee_transaction_id = referee_grants[0].get("transaction_id")

    # ------------------------------------------------------------------
    # 3. Mark as REWARDED if any bubbles were distributed
    # ------------------------------------------------------------------
    total_bubbles = referrer_bubbles + referee_bubbles
    if total_bubbles > 0:
        record.status = ReferralStatus.REWARDED
        record.rewarded_at = now
    else:
        logger.warning(
            "No reward rules matched for referral %s — check that "
            "referral.qualified rules are active and seeded.",
            record.id,
        )

    # ------------------------------------------------------------------
    # 4. Update referral code stats + check ambassador milestone
    # ------------------------------------------------------------------
    code_result = await db.execute(
        select(ReferralCode).where(ReferralCode.id == record.referral_code_id)
    )
    referral_code = code_result.scalar_one_or_none()
    if referral_code:
        referral_code.successful_referrals += 1

        # Ambassador milestone — emit referral.milestone event
        if referral_code.successful_referrals == AMBASSADOR_THRESHOLD:
            milestone_event = WalletEvent(
                event_id=_uuid.uuid4(),
                event_type="referral.milestone",
                member_auth_id=record.referrer_auth_id,
                service_source="wallet",
                occurred_at=now,
                event_data={
                    "milestone_count": referral_code.successful_referrals,
                    "referral_code": referral_code.code,
                },
                idempotency_key=f"referral-ambassador-{record.referrer_auth_id}-{referral_code.id}",
            )
            db.add(milestone_event)
            await db.flush()

            milestone_grants = await process_event(milestone_event, db)
            if milestone_grants:
                logger.info(
                    "Ambassador milestone awarded to %s (%d Bubbles)",
                    record.referrer_auth_id,
                    sum(g["bubbles"] for g in milestone_grants),
                )


# ---------------------------------------------------------------------------
# Stats & history
# ---------------------------------------------------------------------------
async def get_referral_stats(auth_id: str, db: AsyncSession) -> dict:
    """Get referral statistics for a member."""
    # Get the referral code
    code_result = await db.execute(
        select(ReferralCode).where(
            ReferralCode.member_auth_id == auth_id,
            ReferralCode.is_active.is_(True),
        )
    )
    code = code_result.scalar_one_or_none()

    # Count records by status
    result = await db.execute(
        select(
            ReferralRecord.status,
            func.count(ReferralRecord.id).label("count"),
        )
        .where(ReferralRecord.referrer_auth_id == auth_id)
        .group_by(ReferralRecord.status)
    )
    status_counts = {row[0].value: row[1] for row in result.all()}

    # Total bubbles earned from referrals
    bubbles_result = await db.execute(
        select(
            func.coalesce(func.sum(ReferralRecord.referrer_reward_bubbles), 0)
        ).where(ReferralRecord.referrer_auth_id == auth_id)
    )
    total_bubbles = bubbles_result.scalar()

    total_invited = sum(status_counts.values())
    successful = status_counts.get("rewarded", 0) + status_counts.get("qualified", 0)

    return {
        "total_referrals_sent": total_invited,
        "registered": status_counts.get("registered", 0),
        "qualified": status_counts.get("qualified", 0),
        "rewarded": status_counts.get("rewarded", 0),
        "pending": status_counts.get("pending", 0),
        "total_bubbles_earned": total_bubbles,
        "is_ambassador": successful >= AMBASSADOR_THRESHOLD,
        "referrals_to_ambassador": max(0, AMBASSADOR_THRESHOLD - successful),
        "max_referrals": code.max_uses if code else MAX_REFERRALS_PER_CODE,
        "remaining_referrals": (
            max(
                0,
                ((code.max_uses or MAX_REFERRALS_PER_CODE) - code.uses_count)
                if code
                else MAX_REFERRALS_PER_CODE,
            )
        ),
    }


async def get_referral_history(
    auth_id: str, db: AsyncSession, *, skip: int = 0, limit: int = 20
) -> list[ReferralRecord]:
    """Get paginated referral history for a member."""
    result = await db.execute(
        select(ReferralRecord)
        .where(ReferralRecord.referrer_auth_id == auth_id)
        .order_by(ReferralRecord.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())
