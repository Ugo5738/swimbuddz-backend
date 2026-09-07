"""Explicit Session fixtures for actual-schedule pricing tests."""

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4


def attach_schedule(plan, first_date, *, count=13, fee=500_000):
    pool = uuid4()
    links, rows = [], {}
    for index in range(count):
        starts = datetime.combine(
            first_date + timedelta(days=index * 7), time(9), tzinfo=timezone.utc
        )
        session_id = uuid4()
        links.append(
            SimpleNamespace(
                session_id=session_id, pool_id=pool, fee_kobo=fee, starts_at=starts
            )
        )
        rows[str(session_id)] = {
            "id": str(session_id),
            "pool_id": str(pool),
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(hours=2)).isoformat(),
            "status": "scheduled",
            "session_type": "club",
            "fee_kobo": fee,
            "timezone": "Africa/Lagos",
        }
    plan.session_links, plan._actual_session_rows = links, rows
    return plan
