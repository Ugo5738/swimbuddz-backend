"""Internal event ingestion endpoint for the rewards engine.

Called by other SwimBuddz services to submit events that may trigger
automatic Bubble rewards (attendance milestones, topups, etc.).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.session import get_async_db
from services.wallet_service.models.rewards import WalletEvent
from services.wallet_service.schemas.rewards import (
    EventIngestRequest,
    EventIngestResponse,
    RewardGrantItem,
)
from services.wallet_service.services.rewards_engine import process_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/wallet", tags=["internal-rewards"])


@router.post("/events", response_model=EventIngestResponse)
async def ingest_event(
    body: EventIngestRequest,
    db: AsyncSession = Depends(get_async_db),
):
    """Submit an event for rewards processing.

    Deduplicates by event_id and idempotency_key. If the event was
    already processed, returns the original result.
    """
    # Deduplicate by event_id
    result = await db.execute(
        select(WalletEvent).where(WalletEvent.event_id == body.event_id)
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info("Duplicate event_id=%s, returning existing result", body.event_id)
        return EventIngestResponse(
            event_id=existing.event_id,
            accepted=True,
            rewards_granted=existing.rewards_granted,
            rewards=[],  # We don't store individual grants on the event
        )

    # Also check by idempotency_key
    result = await db.execute(
        select(WalletEvent).where(WalletEvent.idempotency_key == body.idempotency_key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        logger.info(
            "Duplicate idempotency_key=%s, returning existing result",
            body.idempotency_key,
        )
        return EventIngestResponse(
            event_id=existing.event_id,
            accepted=True,
            rewards_granted=existing.rewards_granted,
            rewards=[],
        )

    # Create the event record
    event = WalletEvent(
        event_id=body.event_id,
        event_type=body.event_type,
        member_auth_id=body.member_auth_id,
        member_id=body.member_id,
        service_source=body.service_source,
        occurred_at=body.occurred_at,
        event_data=body.event_data,
        idempotency_key=body.idempotency_key,
    )
    db.add(event)
    try:
        await db.flush()
    except IntegrityError:
        # Race condition: another concurrent request inserted first.
        # Roll back and return the existing record.
        await db.rollback()
        logger.info(
            "Concurrent duplicate for idempotency_key=%s, returning existing result",
            body.idempotency_key,
        )
        result = await db.execute(
            select(WalletEvent).where(
                WalletEvent.idempotency_key == body.idempotency_key
            )
        )
        existing = result.scalar_one_or_none()
        return EventIngestResponse(
            event_id=existing.event_id if existing else body.event_id,
            accepted=True,
            rewards_granted=existing.rewards_granted if existing else 0,
            rewards=[],
        )

    # Process through the rewards engine
    try:
        grants = await process_event(event, db)
    except Exception:
        logger.exception("Error processing event %s", body.event_id)
        event.processing_error = "Unexpected error during processing"
        event.processed = True
        await db.flush()
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail="Event accepted but processing failed. Will retry.",
        )

    await db.commit()

    return EventIngestResponse(
        event_id=event.event_id,
        accepted=True,
        rewards_granted=len(grants),
        rewards=[
            RewardGrantItem(rule_name=g["rule_name"], bubbles=g["bubbles"])
            for g in grants
        ],
    )
