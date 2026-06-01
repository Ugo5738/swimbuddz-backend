"""Replay dead-lettered ledger posts (payments_service).

Re-posts every `pending` row in ledger_post_failures to ledger_service. The
ledger's idempotency_key dedupes, so replaying an entry that actually did post
is harmless. Run in-container:

    docker compose exec payments-service python scripts/ledger/replay_ledger_failures.py

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    replayed = still_failing = 0
    try:
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(LedgerPostFailure).where(
                            LedgerPostFailure.status == "pending"
                        )
                    )
                )
                .scalars()
                .all()
            )
            print(f"{len(rows)} pending dead-letter row(s)")
            for row in rows:
                try:
                    # payload holds the exact post_journal_entry kwargs (minus
                    # calling_service, which we pass explicitly).
                    await post_journal_entry(calling_service="payments", **row.payload)
                    row.status = "replayed"
                    replayed += 1
                except Exception as exc:  # noqa: BLE001 — keep going through the batch
                    row.attempts += 1
                    row.last_error = str(exc)
                    still_failing += 1
                    logger.warning("Replay failed for %s: %s", row.idempotency_key, exc)
            await session.commit()
        print(f"Replayed {replayed}, still failing {still_failing}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
