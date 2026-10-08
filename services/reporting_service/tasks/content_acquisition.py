"""Refresh content-sourced registrations through existing Reporting -> Members API.

No calls from Academy, Communications, Payments or Events; no shared tables.
"""

from collections import Counter, defaultdict
from datetime import datetime, time, timezone

from libs.common.service_client.core import internal_post
from datetime import timedelta
from uuid import uuid4
from uuid import UUID

from sqlalchemy import delete
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
        get_settings().MEMBERS_SERVICE_URL, "community", start, end, strict=True
    )
    counter: Counter[UUID] = Counter()
    auth_origins: dict[str, UUID] = {}
    for member in members:
        origin = member.get("content_source") or ""
        if not origin.startswith("content:"):
            continue
        try:
            content_id = UUID(origin.removeprefix("content:"))
        except ValueError:
            continue
        counter[content_id] += 1
        if member.get("member_auth_id"):
            auth_origins[str(member["member_auth_id"])] = content_id

    # Only Reporting aggregates the independent Members and Payments contracts.
    # The Payments service never knows about content or member acquisition.
    totals: dict[UUID, dict] = defaultdict(
        lambda: {"paying_members": set(), "payment_count": 0, "paid_amount_ngn": 0.0}
    )
    from libs.common.config import get_settings as settings_factory

    auth_ids = list(auth_origins)
    for start_index in range(0, len(auth_ids), 500):
        batch = auth_ids[start_index : start_index + 500]
        response = await internal_post(
            service_url=settings_factory().PAYMENTS_SERVICE_URL,
            path="/internal/payments/reports/attributed-payments",
            calling_service="reporting",
            json={
                "member_auth_ids": batch,
                "date_from": datetime.combine(start, time.min, tzinfo=timezone.utc).isoformat(),
                "date_to": datetime.combine(end, time.max, tzinfo=timezone.utc).isoformat(),
            },
        )
        response.raise_for_status()
        for item in response.json().get("items", []):
            auth_id = item["member_auth_id"]
            content_id = auth_origins.get(auth_id)
            if content_id is None:
                continue
            bucket = totals[content_id]
            bucket["paying_members"].add(auth_id)
            bucket["payment_count"] += int(item["payment_count"])
            bucket["paid_amount_ngn"] += float(item["amount_ngn"])

    async with AsyncSessionLocal() as db:
        # Recompute the entire period so corrected/deleted registrations cannot
        # leave stale positive counts behind.
        await db.execute(
            delete(ContentAcquisitionSnapshot).where(
                ContentAcquisitionSnapshot.period_start == start,
                ContentAcquisitionSnapshot.period_end == end,
            )
        )
        for content_id, count in counter.items():
            statement = insert(ContentAcquisitionSnapshot).values(
                id=uuid4(),
                content_id=content_id,
                period_start=start,
                period_end=end,
                registrations=count,
                paying_members=len(totals[content_id]["paying_members"]),
                payment_count=totals[content_id]["payment_count"],
                paid_amount_ngn=totals[content_id]["paid_amount_ngn"],
                computed_at=utc_now(),
                source="members_registration",
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_content_acquisition_period",
                set_={
                    "registrations": count,
                    "paying_members": len(totals[content_id]["paying_members"]),
                    "payment_count": totals[content_id]["payment_count"],
                    "paid_amount_ngn": totals[content_id]["paid_amount_ngn"],
                    "computed_at": utc_now(),
                },
            )
            await db.execute(statement)
        await db.commit()
    return len(counter)
