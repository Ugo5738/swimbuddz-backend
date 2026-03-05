"""Automated rewards engine — processes events and grants Bubbles.

Follows the pipeline defined in REWARDS_ENGINE_DESIGN.md Section 3.4:
1. Deduplicate event
2. Find matching active rules
3. Evaluate conditions and caps
4. Credit wallet via wallet_ops.credit_wallet()
5. Record reward history
6. Mark event processed
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.enums import TransactionType
from services.wallet_service.models.rewards import (
    MemberRewardHistory,
    RewardRule,
    WalletEvent,
)
from services.wallet_service.services.abuse_detector import check_for_abuse
from services.wallet_service.services.cap_checker import (
    check_lifetime_cap,
    check_period_cap,
    compute_period_key,
)
from services.wallet_service.services.wallet_ops import credit_wallet

logger = logging.getLogger(__name__)


def evaluate_conditions(trigger_config: Optional[dict], event_data: dict) -> bool:
    """Evaluate rule trigger conditions against event data.

    Supports simple condition patterns:
    - {"min_<key>": N} — event_data[key] >= N
    - {"max_<key>": N} — event_data[key] <= N
    - {"<key>": value} — exact match
    - {} or None — always matches (no conditions)
    """
    if not trigger_config:
        return True

    for key, expected in trigger_config.items():
        if key.startswith("min_"):
            field = key[4:]
            actual = event_data.get(field)
            if actual is None or actual < expected:
                return False
        elif key.startswith("max_"):
            field = key[4:]
            actual = event_data.get(field)
            if actual is None or actual > expected:
                return False
        else:
            actual = event_data.get(key)
            if actual != expected:
                return False

    return True


async def get_matching_rules(db: AsyncSession, event_type: str) -> list[RewardRule]:
    """Fetch active rules for an event type, ordered by priority DESC."""
    result = await db.execute(
        select(RewardRule)
        .where(RewardRule.event_type == event_type, RewardRule.is_active.is_(True))
        .order_by(RewardRule.priority.desc())
    )
    return list(result.scalars().all())


async def process_event(event: WalletEvent, db: AsyncSession) -> list[dict]:
    """Process an ingested event against all matching reward rules.

    Returns a list of dicts: [{"rule_name": ..., "bubbles": ...}, ...]
    """
    grants: list[dict] = []
    granted_rule_ids: set[uuid.UUID] = set()

    matching_rules = await get_matching_rules(db, event.event_type)
    if not matching_rules:
        logger.debug("No active rules for event_type=%s", event.event_type)
        event.processed = True
        event.processed_at = datetime.now(timezone.utc)
        await db.flush()
        return grants

    for rule in matching_rules:
        try:
            # 1. Check replaces_rule_id — skip if a higher-priority rule
            #    that this one replaces has already granted
            if rule.replaces_rule_id and rule.replaces_rule_id in granted_rule_ids:
                # This rule supersedes an already-granted rule — that shouldn't
                # happen since higher priority rules are processed first.
                # But if the replaced rule was already granted, skip this one.
                pass  # Fall through — the replaced rule is the lower-priority one

            # Actually: replaces_rule_id means THIS rule replaces another.
            # If this rule (higher priority) fires, we should skip the
            # lower-priority rule it replaces. We track granted_rule_ids
            # and check below.

            # 2. Evaluate conditions
            if not evaluate_conditions(rule.trigger_config, event.event_data):
                continue

            # 3. Check admin confirmation requirement
            if rule.requires_admin_confirmation:
                if not event.event_data.get("admin_confirmed", False):
                    logger.debug(
                        "Rule %s requires admin confirmation, skipping",
                        rule.rule_name,
                    )
                    continue

            # 4. Check caps
            if not await check_lifetime_cap(db, event.member_auth_id, rule):
                continue
            if not await check_period_cap(db, event.member_auth_id, rule):
                continue

            # 5. Check if this rule is replaced by a higher-priority rule
            #    that already fired for this event
            replaced_by_higher = False
            for granted_id in granted_rule_ids:
                # Check if any already-granted rule replaces this one
                for prev_rule in matching_rules:
                    if (
                        prev_rule.id == granted_id
                        and prev_rule.replaces_rule_id == rule.id
                    ):
                        replaced_by_higher = True
                        break
                if replaced_by_higher:
                    break
            if replaced_by_higher:
                logger.debug(
                    "Rule %s replaced by higher-priority rule, skipping",
                    rule.rule_name,
                )
                continue

            # 6. Grant reward
            idempotency_key = f"reward-{rule.id}-{event.event_id}"
            description = rule.render_description(event.event_data)

            txn = await credit_wallet(
                db,
                member_auth_id=event.member_auth_id,
                amount=rule.reward_bubbles,
                idempotency_key=idempotency_key,
                transaction_type=TransactionType.REWARD,
                description=description,
                service_source="rewards_engine",
                reference_type="reward_rule",
                reference_id=str(rule.id),
                initiated_by="rewards_engine",
                metadata={
                    "rule_name": rule.rule_name,
                    "event_type": event.event_type,
                    "event_id": str(event.event_id),
                },
            )

            # 7. Record history for cap tracking
            period_key = None
            if rule.period is not None:
                period_key = compute_period_key(rule.period)

            history = MemberRewardHistory(
                member_auth_id=event.member_auth_id,
                reward_rule_id=rule.id,
                wallet_event_id=event.id,
                transaction_id=txn.id,
                bubbles_awarded=rule.reward_bubbles,
                period_key=period_key,
            )
            db.add(history)
            await db.flush()

            granted_rule_ids.add(rule.id)
            grants.append(
                {
                    "rule_name": rule.rule_name,
                    "bubbles": rule.reward_bubbles,
                    "transaction_id": txn.id,
                }
            )

            logger.info(
                "Granted %d Bubbles to %s via rule %s (event=%s)",
                rule.reward_bubbles,
                event.member_auth_id,
                rule.rule_name,
                event.event_id,
            )

            # Best-effort abuse detection — never blocks the grant
            try:
                await check_for_abuse(event.member_auth_id, rule, event, db)
            except Exception:
                logger.exception("Abuse check failed for event %s", event.event_id)

        except Exception:
            logger.exception(
                "Error processing rule %s for event %s",
                rule.rule_name,
                event.event_id,
            )
            event.processing_error = (
                f"Error in rule {rule.rule_name}: see logs for details"
            )

    # 8. Mark event as processed
    event.processed = True
    event.processed_at = datetime.now(timezone.utc)
    event.rewards_granted = len(grants)
    await db.flush()

    return grants
