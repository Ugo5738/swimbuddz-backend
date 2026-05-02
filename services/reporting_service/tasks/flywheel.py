"""Flywheel snapshot computation tasks.

These tasks call cross-service HTTP APIs (academy, members, sessions, wallet)
via ``libs.common.service_client`` to gather data, compute the relevant
metrics, and persist them to the reporting_service snapshot tables.

Run via ARQ worker (see ``services.reporting_service.tasks.worker``).
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from libs.db.session import AsyncSessionLocal
from services.reporting_service.models import (
    CohortFillSnapshot,
    FunnelConversionSnapshot,
    FunnelStage,
    WalletEcosystemSnapshot,
)

logger = get_logger(__name__)

# Observation windows for funnel conversions (per FLYWHEEL_METRICS_DESIGN.md)
COMMUNITY_TO_CLUB_WINDOW_DAYS = 180
CLUB_TO_ACADEMY_WINDOW_DAYS = 270
COMMUNITY_TO_ACADEMY_WINDOW_DAYS = 365

# Wallet ecosystem analysis window
WALLET_ANALYSIS_WINDOW_DAYS = 90

CALLER = "reporting"


# ─────────────────────────────────────────────────────────────────────────────
# COHORT FILL SNAPSHOT — operational metric, refreshed daily
# ─────────────────────────────────────────────────────────────────────────────


async def compute_cohort_fill_snapshots() -> int:
    """Snapshot fill state for all OPEN and ACTIVE cohorts.

    Calls academy_service for cohorts and their enrollment counts.

    Returns:
        Count of cohort snapshots created.
    """
    logger.info("compute_cohort_fill_snapshots: starting")
    settings = get_settings()

    cohorts = await _fetch_open_and_active_cohorts(settings.ACADEMY_SERVICE_URL)
    logger.info(f"compute_cohort_fill_snapshots: {len(cohorts)} cohorts to snapshot")

    if not cohorts:
        return 0

    now = utc_now()
    snapshots: list[CohortFillSnapshot] = []
    for cohort in cohorts:
        enrollment_counts = await _fetch_cohort_enrollment_counts(
            settings.ACADEMY_SERVICE_URL, cohort["id"]
        )
        capacity = int(cohort.get("capacity", 0) or 0)
        active = enrollment_counts.get("active", 0)
        pending = enrollment_counts.get("pending_approval", 0)
        waitlist = enrollment_counts.get("waitlist", 0)
        filled = active + pending
        fill_rate = (filled / capacity) if capacity > 0 else 0.0

        starts_at = _parse_iso_dt(cohort.get("start_date"))
        ends_at = _parse_iso_dt(cohort.get("end_date"))
        days_until_start = (starts_at.date() - now.date()).days if starts_at else None

        snapshots.append(
            CohortFillSnapshot(
                cohort_id=cohort["id"],
                cohort_name=cohort.get("name", "Unnamed Cohort"),
                program_name=cohort.get("program_name"),
                capacity=capacity,
                active_enrollments=active,
                pending_approvals=pending,
                waitlist_count=waitlist,
                fill_rate=fill_rate,
                starts_at=starts_at,
                ends_at=ends_at,
                cohort_status=str(cohort.get("status", "")).lower(),
                days_until_start=days_until_start,
                snapshot_taken_at=now,
            )
        )

    async with AsyncSessionLocal() as db:
        db.add_all(snapshots)
        await db.commit()

    logger.info(f"compute_cohort_fill_snapshots: persisted {len(snapshots)} snapshots")
    return len(snapshots)


async def _fetch_open_and_active_cohorts(academy_url: str) -> list[dict]:
    """Fetch cohorts in OPEN or ACTIVE status from academy_service."""
    try:
        resp = await internal_get(
            service_url=academy_url,
            path="/internal/academy/cohorts",
            calling_service=CALLER,
            params={"status": "open,active"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("cohorts", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.warning(f"_fetch_open_and_active_cohorts failed: {e}")
        return []


async def _fetch_cohort_enrollment_counts(
    academy_url: str, cohort_id: str
) -> dict[str, int]:
    """Fetch enrollment counts grouped by status for a cohort."""
    try:
        resp = await internal_get(
            service_url=academy_url,
            path=f"/internal/academy/cohorts/{cohort_id}/enrollment-counts",
            calling_service=CALLER,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"_fetch_cohort_enrollment_counts({cohort_id}) failed: {e}")
        return {"active": 0, "pending_approval": 0, "waitlist": 0}


# ─────────────────────────────────────────────────────────────────────────────
# FUNNEL CONVERSION SNAPSHOT — community→club, club→academy
# ─────────────────────────────────────────────────────────────────────────────


async def compute_funnel_conversions(period_label: str | None = None) -> int:
    """Compute conversion rates for the configured funnel stages.

    Args:
        period_label: Cohort period to analyse, e.g. "2026-Q1". Defaults to
            the most recently completed quarter.

    Returns:
        Count of funnel snapshots created.
    """
    logger.info(f"compute_funnel_conversions: period={period_label}")

    period_label, period_start, period_end = _resolve_period(period_label)

    snapshots: list[FunnelConversionSnapshot] = []
    snapshots.append(
        await _compute_one_funnel(
            FunnelStage.COMMUNITY_TO_CLUB,
            period_label,
            period_start,
            period_end,
            COMMUNITY_TO_CLUB_WINDOW_DAYS,
        )
    )
    snapshots.append(
        await _compute_one_funnel(
            FunnelStage.CLUB_TO_ACADEMY,
            period_label,
            period_start,
            period_end,
            CLUB_TO_ACADEMY_WINDOW_DAYS,
        )
    )
    snapshots.append(
        await _compute_one_funnel(
            FunnelStage.COMMUNITY_TO_ACADEMY,
            period_label,
            period_start,
            period_end,
            COMMUNITY_TO_ACADEMY_WINDOW_DAYS,
        )
    )

    async with AsyncSessionLocal() as db:
        db.add_all(snapshots)
        await db.commit()

    logger.info(f"compute_funnel_conversions: persisted {len(snapshots)} snapshots")
    return len(snapshots)


async def _compute_one_funnel(
    stage: FunnelStage,
    period_label: str,
    period_start: date,
    period_end: date,
    window_days: int,
) -> FunnelConversionSnapshot:
    """Compute conversion rate for a single funnel stage in the given period."""
    settings = get_settings()
    members_url = settings.MEMBERS_SERVICE_URL

    # 1. Source members: those who entered the SOURCE layer in [period_start, period_end]
    if stage == FunnelStage.COMMUNITY_TO_CLUB:
        source_tier = "community"
        target_tier = "club"
    elif stage == FunnelStage.CLUB_TO_ACADEMY:
        source_tier = "club"
        target_tier = "academy"
    else:  # COMMUNITY_TO_ACADEMY
        source_tier = "community"
        target_tier = "academy"

    source_members = await _fetch_members_who_joined_tier(
        members_url, source_tier, period_start, period_end
    )
    source_count = len(source_members)

    # 2. Of those, how many crossed to TARGET tier within window_days of joining source
    converted_count = 0
    breakdown: dict[str, int] = defaultdict(int)
    for member in source_members:
        joined_source_at = _parse_iso_dt(member.get("source_joined_at"))
        if not joined_source_at:
            continue
        deadline = joined_source_at + timedelta(days=window_days)
        crossed = await _check_member_crossed_to_tier(
            members_url, member["id"], target_tier, joined_source_at, deadline
        )
        if crossed:
            converted_count += 1
            acquisition = member.get("acquisition_source", "unknown")
            breakdown[acquisition] += 1

    rate = (converted_count / source_count) if source_count > 0 else 0.0

    return FunnelConversionSnapshot(
        funnel_stage=stage,
        cohort_period=period_label,
        period_start=period_start,
        period_end=period_end,
        observation_window_days=window_days,
        source_count=source_count,
        converted_count=converted_count,
        conversion_rate=rate,
        breakdown_by_source=dict(breakdown) if breakdown else None,
    )


async def _fetch_members_who_joined_tier(
    members_url: str, tier: str, start: date, end: date
) -> list[dict]:
    """Members who entered the given tier between start and end.

    Expected response: list of dicts with keys: id, source_joined_at, acquisition_source.

    NOTE: This relies on a new internal endpoint on members_service:
        GET /internal/members/joined-tier?tier=...&from=...&to=...
    Adding that endpoint is a prerequisite — see follow-up doc.
    """
    try:
        resp = await internal_get(
            service_url=members_url,
            path="/internal/members/joined-tier",
            calling_service=CALLER,
            params={
                "tier": tier,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("members", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.warning(
            f"_fetch_members_who_joined_tier({tier}, {start}, {end}) failed: {e}"
        )
        return []


async def _check_member_crossed_to_tier(
    members_url: str,
    member_id: str,
    target_tier: str,
    after: datetime,
    before: datetime,
) -> bool:
    """Check if a member entered ``target_tier`` between ``after`` and ``before``."""
    try:
        resp = await internal_get(
            service_url=members_url,
            path=f"/internal/members/{member_id}/tier-history",
            calling_service=CALLER,
        )
        resp.raise_for_status()
        history = resp.json().get("entries", [])
        for entry in history:
            tier = entry.get("tier")
            entered_at = _parse_iso_dt(entry.get("entered_at"))
            if (
                tier == target_tier
                and entered_at is not None
                and after <= entered_at <= before
            ):
                return True
        return False
    except Exception as e:
        logger.debug(f"_check_member_crossed_to_tier failed for {member_id}: {e}")
        return False


def _resolve_period(period_label: str | None) -> tuple[str, date, date]:
    """Resolve a period label like '2026-Q1' to (label, start_date, end_date).

    Defaults to the most recently completed calendar quarter.
    """
    if period_label:
        year_str, q_str = period_label.split("-Q")
        year, q = int(year_str), int(q_str)
    else:
        today = utc_now().date()
        # Most recently COMPLETED quarter
        current_q = (today.month - 1) // 3 + 1
        if current_q == 1:
            year, q = today.year - 1, 4
        else:
            year, q = today.year, current_q - 1

    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, end_month + 1, 1) - timedelta(days=1)

    return f"{year}-Q{q}", start, end


# ─────────────────────────────────────────────────────────────────────────────
# WALLET ECOSYSTEM SNAPSHOT — cross-service spend rate
# ─────────────────────────────────────────────────────────────────────────────


async def compute_wallet_ecosystem_snapshot(
    window_days: int = WALLET_ANALYSIS_WINDOW_DAYS,
) -> WalletEcosystemSnapshot:
    """Compute wallet cross-service ecosystem stats over the trailing window.

    Cross-service user = a member who has DEBIT transactions tagged with ≥2
    distinct ``service_source`` values within the window.
    """
    logger.info(f"compute_wallet_ecosystem_snapshot: window_days={window_days}")
    settings = get_settings()

    period_end = utc_now().date()
    period_start = period_end - timedelta(days=window_days)

    stats = await _fetch_wallet_ecosystem_aggregates(
        settings.WALLET_SERVICE_URL, period_start, period_end
    )

    active = stats.get("active_wallet_users", 0)
    cross = stats.get("cross_service_users", 0)
    single = stats.get("single_service_users", max(active - cross, 0))
    rate = (cross / active) if active > 0 else 0.0

    snapshot = WalletEcosystemSnapshot(
        period_start=period_start,
        period_end=period_end,
        period_days=window_days,
        active_wallet_users=active,
        single_service_users=single,
        cross_service_users=cross,
        cross_service_rate=rate,
        total_bubbles_spent=stats.get("total_bubbles_spent", 0),
        total_topup_bubbles=stats.get("total_topup_bubbles", 0),
        spend_distribution=stats.get("spend_distribution"),
    )

    async with AsyncSessionLocal() as db:
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

    logger.info(
        f"compute_wallet_ecosystem_snapshot: cross_rate={snapshot.cross_service_rate:.2%}"
    )
    return snapshot


async def _fetch_wallet_ecosystem_aggregates(
    wallet_url: str, period_start: date, period_end: date
) -> dict[str, Any]:
    """Fetch aggregated wallet ecosystem stats from wallet_service.

    NOTE: This relies on a new internal endpoint on wallet_service:
        GET /internal/wallet/ecosystem-stats?from=...&to=...
    Adding that endpoint is a prerequisite — see follow-up doc.
    """
    try:
        resp = await internal_get(
            service_url=wallet_url,
            path="/internal/wallet/ecosystem-stats",
            calling_service=CALLER,
            params={
                "from": period_start.isoformat(),
                "to": period_end.isoformat(),
            },
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"_fetch_wallet_ecosystem_aggregates failed: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_iso_dt(value: Any) -> datetime | None:
    """Parse ISO 8601 datetime string to datetime, or None on failure."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Combined refresh — runs all three
# ─────────────────────────────────────────────────────────────────────────────


async def refresh_all_flywheel_snapshots() -> dict[str, int | str]:
    """Run all three flywheel snapshot tasks. Returns a summary dict."""
    logger.info("refresh_all_flywheel_snapshots: starting")
    summary: dict[str, int | str] = {}

    try:
        summary["cohort_snapshots"] = await compute_cohort_fill_snapshots()
    except Exception as e:
        logger.exception("compute_cohort_fill_snapshots failed")
        summary["cohort_snapshots"] = f"error: {e}"

    try:
        summary["funnel_snapshots"] = await compute_funnel_conversions()
    except Exception as e:
        logger.exception("compute_funnel_conversions failed")
        summary["funnel_snapshots"] = f"error: {e}"

    try:
        snap = await compute_wallet_ecosystem_snapshot()
        summary["wallet_snapshot_id"] = str(snap.id)
    except Exception as e:
        logger.exception("compute_wallet_ecosystem_snapshot failed")
        summary["wallet_snapshot_id"] = f"error: {e}"

    logger.info(f"refresh_all_flywheel_snapshots: {summary}")
    return summary
