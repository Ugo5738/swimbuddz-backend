"""Replay dead-lettered ledger posts (payments + wallet).

Re-posts every `pending` row in each service's dead-letter table to
ledger_service. The ledger's idempotency_key dedupes, so replaying an entry that
actually did post is harmless. Run in-container (any service image carries all
code + DATABASE_URL):

    docker exec -w /app swimbuddz_payments \
        python scripts/ledger/replay_ledger_failures.py

A row that re-fails keeps status=pending with an incremented attempt count and
the latest error, so repeated runs converge (or surface a permanent mapping bug
via last_error).
"""

from __future__ import annotations

import asyncio

from libs.common.config import get_settings
from libs.common.ledger_client import post_journal_entry
from libs.common.logging import get_logger
from services.payments_service.models.ledger_failure import LedgerPostFailure
from services.wallet_service.models.ledger_failure import WalletLedgerPostFailure
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = get_logger(__name__)


async def _replay(
    session: AsyncSession, model, calling_service: str
) -> tuple[int, int]:
    """Re-post pending rows of one dead-letter table. Returns (replayed, failed)."""
    rows = (
        (await session.execute(select(model).where(model.status == "pending")))
        .scalars()
        .all()
    )
    print(f"{len(rows)} pending {calling_service} dead-letter row(s)")
    replayed = still_failing = 0
    for row in rows:
        try:
            # payload holds the exact post_journal_entry kwargs (minus
            # calling_service, which we pass explicitly).
            await post_journal_entry(calling_service=calling_service, **row.payload)
            row.status = "replayed"
            replayed += 1
        except Exception as exc:  # noqa: BLE001 — keep going through the batch
            row.attempts += 1
            row.last_error = str(exc)
            still_failing += 1
            logger.warning("Replay failed for %s: %s", row.idempotency_key, exc)
    await session.commit()
    return replayed, still_failing


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            p_ok, p_fail = await _replay(session, LedgerPostFailure, "payments")
            w_ok, w_fail = await _replay(session, WalletLedgerPostFailure, "wallet")
        print(f"Replayed {p_ok + w_ok}, still failing {p_fail + w_fail}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
