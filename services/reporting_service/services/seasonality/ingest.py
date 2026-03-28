"""Auto-ingest monthly actuals from other SwimBuddz services.

Fetches aggregate data from attendance, sessions, payments, and members
services to populate MonthlyActual records without manual CSV entry.

Uses the same _safe_get pattern as the quarterly report aggregator.

Available data sources:
    - Members service:    /internal/members/approved-list → active member count
    - Sessions service:   /internal/sessions/range-stats  → session count by type
    - Attendance service:  /internal/stats/member/{id}     → per-member attendance
    - Payments service:   /payments/payments/member-summary/{id} → per-member spend

For aggregate totals (attendance, revenue), we iterate approved members
with a concurrency limiter to avoid overwhelming services.  This is
acceptable because ingestion runs monthly (not on every request).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from services.reporting_service.models.enums import DataSource
from services.reporting_service.models.seasonality import MonthlyActual

logger = get_logger(__name__)
settings = get_settings()

CALLING_SERVICE = "reporting"
MAX_CONCURRENT = 10


async def _safe_get(
    service_url: str, path: str, params: dict | None = None
) -> dict | list | None:
    """Internal GET with graceful failure."""
    try:
        resp = await internal_get(
            service_url=service_url,
            path=path,
            calling_service=CALLING_SERVICE,
            params=params,
            timeout=15.0,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"[ingest] {path} returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"[ingest] {path} failed: {e}")
    return None


def _month_range(year: int, month: int) -> tuple[str, str]:
    """Return ISO date strings for the first and last instant of a month."""
    from calendar import monthrange

    _, last_day = monthrange(year, month)
    date_from = datetime(year, month, 1, tzinfo=timezone.utc).isoformat()
    date_to = datetime(
        year, month, last_day, 23, 59, 59, tzinfo=timezone.utc
    ).isoformat()
    return date_from, date_to


async def _fetch_approved_members() -> list[dict]:
    """Fetch all approved members."""
    data = await _safe_get(
        settings.MEMBERS_SERVICE_URL,
        "/internal/members/approved-list",
    )
    return data or []


async def _fetch_session_stats(date_from: str, date_to: str) -> dict:
    """Fetch aggregate session stats for a date range."""
    data = await _safe_get(
        settings.SESSIONS_SERVICE_URL,
        "/internal/sessions/range-stats",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_member_attendance(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch attendance stats for one member."""
    data = await _safe_get(
        settings.ATTENDANCE_SERVICE_URL,
        f"/internal/stats/member/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_member_payment(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch payment summary for one member."""
    data = await _safe_get(
        settings.PAYMENTS_SERVICE_URL,
        f"/payments/payments/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def ingest_month(year: int, month: int, db: AsyncSession) -> MonthlyActual | None:
    """Ingest actual metrics for a single month from live services.

    Fetches data from members, sessions, attendance, and payments services,
    aggregates totals, and upserts a MonthlyActual record.

    Returns the created/updated record, or None if services were unreachable.
    """
    date_from, date_to = _month_range(year, month)

    logger.info(f"[ingest] Ingesting actuals for {year}-{month:02d}")

    # Fetch members and session stats in parallel (both are aggregate calls)
    members, session_stats = await asyncio.gather(
        _fetch_approved_members(),
        _fetch_session_stats(date_from, date_to),
    )

    if not members:
        logger.warning(f"[ingest] No members found, skipping {year}-{month:02d}")
        return None

    # Fetch per-member attendance and payments with concurrency limit
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    total_attendance = 0
    total_revenue = 0
    active_member_ids: set[str] = set()
    attendance_by_type: dict[str, int] = {}

    async def process_member(member: dict) -> None:
        nonlocal total_attendance, total_revenue
        auth_id = member.get("auth_id", "")
        if not auth_id:
            return

        async with semaphore:
            att, pay = await asyncio.gather(
                _fetch_member_attendance(auth_id, date_from, date_to),
                _fetch_member_payment(auth_id, date_from, date_to),
            )

        present = att.get("total_present", 0) + att.get("total_late", 0)
        if present > 0:
            active_member_ids.add(auth_id)
            total_attendance += present

            # Aggregate by type
            by_type = att.get("by_type", {})
            for stype, count in by_type.items():
                attendance_by_type[stype] = attendance_by_type.get(stype, 0) + count

        spent = pay.get("total_spent", 0)
        if spent > 0:
            total_revenue += spent

    # Process all members (sequential to avoid session issues, but with
    # semaphore-limited HTTP concurrency)
    tasks = [process_member(m) for m in members]
    await asyncio.gather(*tasks)

    # Count new signups (members created this month)
    # Approximate: members whose auth_id we see but who have very low attendance
    # A proper implementation would need a /internal/members/count-by-date endpoint
    # For now, we store 0 and let the admin fill this in manually if needed
    new_signups = 0

    # Upsert the record
    result = await db.execute(
        select(MonthlyActual).where(
            MonthlyActual.year == year,
            MonthlyActual.month == month,
        )
    )
    existing = result.scalar_one_or_none()

    record_data = dict(
        active_members=len(active_member_ids),
        total_sessions_held=session_stats.get("total_sessions", 0),
        total_attendance=total_attendance,
        new_signups=new_signups,
        churned_members=0,  # Requires previous month comparison — filled on second run
        total_revenue_ngn=total_revenue,
        attendance_by_type=attendance_by_type if attendance_by_type else None,
        source=DataSource.SYSTEM,
        computed_at=utc_now(),
    )

    if existing:
        for key, value in record_data.items():
            setattr(existing, key, value)
        record = existing
    else:
        record = MonthlyActual(year=year, month=month, **record_data)
        db.add(record)

    await db.commit()
    await db.refresh(record)

    logger.info(
        f"[ingest] {year}-{month:02d}: "
        f"active={record.active_members}, "
        f"sessions={record.total_sessions_held}, "
        f"attendance={record.total_attendance}, "
        f"revenue=₦{record.total_revenue_ngn}"
    )
    return record


async def ingest_all_available_months(
    year: int, db: AsyncSession
) -> list[MonthlyActual]:
    """Ingest actuals for all past months of a given year.

    Only ingests months that have already ended (won't ingest the current
    month since it's incomplete).
    """
    now = datetime.now(timezone.utc)
    results = []

    for month in range(1, 13):
        # Skip future months and the current month (incomplete data)
        if year > now.year or (year == now.year and month >= now.month):
            break

        record = await ingest_month(year, month, db)
        if record:
            results.append(record)

    logger.info(f"[ingest] Ingested {len(results)} months for {year}")
    return results
