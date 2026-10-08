"""Refresh content-sourced registrations through existing Reporting -> Members API.

No calls from Academy, Communications, Payments or Events; no shared tables.
"""

from collections import Counter
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from libs.common.datetime_utils import utc_now
from libs.db.session import AsyncSessionLocal
from services.reporting_service.models.content_acquisition import (
    ContentAcquisitionSnapshot,
)
from services.reporting_service.tasks.flywheel import _fetch_members_who_joined_tier


async def refresh_content_acquisition(days: int = 90) -> int:
    """Upsert aggregate first-party registrations for a bounded rolling window."""
    if not 1 <= days <= 366:
        raise ValueError("days must be between 1 and 366")

    end = utc_now().date()
    start = end - timedelta(days=days - 1)
    from libs.common.config import get_settings

    members = await _fetch_members_who_joined_tier(
        get_settings().MEMBERS_SERVICE_URL, "community", start, end
    )
    counter: Counter[UUID] = Counter()
    for member in members:
        origin = member.get("content_source") or ""
        if not origin.startswith("content:"):
            continue
        try:
            content_id = UUID(origin.removeprefix("content:"))
        except ValueError:
            continue
        counter[content_id] += 1

    async with AsyncSessionLocal() as db:
        for content_id, count in counter.items():
            statement = insert(ContentAcquisitionSnapshot).values(
                id=__import__("uuid").uuid4(),
                content_id=content_id,
                period_start=start,
                period_end=end,
                registrations=count,
                computed_at=utc_now(),
                source="members_registration",
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_content_acquisition_period",
                set_={
                    "registrations": count,
                    "computed_at": utc_now(),
                },
            )
            await db.execute(statement)
        await db.commit()
    return len(counter)
