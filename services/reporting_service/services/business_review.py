"""Quarter-end business review composition.

The business review deliberately reuses domain-owned summaries rather than
persisting a second copy of financial, Academy, Club, or session truth.
Quarterly member/community snapshots remain the frozen engagement layer.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.service_client import internal_get
from services.reporting_service.models import (
    CommunityQuarterlyStats,
    QuarterlySnapshot,
)
from services.reporting_service.schemas.business_review import (
    BusinessReviewFinance,
    BusinessReviewResponse,
    QuarterComparison,
)
from services.reporting_service.services.quarter_utils import (
    quarter_date_range,
    quarter_label,
)

settings = get_settings()
CALLING_SERVICE = "reporting"


async def _safe_json(service_url: str, path: str, params: dict[str, Any]) -> Any:
    try:
        response = await internal_get(
            service_url=service_url,
            path=path,
            calling_service=CALLING_SERVICE,
            params=params,
            timeout=20.0,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def _comparison(current: float | int | None, previous: float | int | None):
    if current is None:
        return QuarterComparison(current=None, previous=previous)
    if previous is None:
        return QuarterComparison(current=current, previous=None)
    delta = current - previous
    delta_pct = None if previous == 0 else round(delta / previous * 100, 1)
    return QuarterComparison(
        current=current, previous=previous, delta=delta, delta_pct=delta_pct
    )


def _naira(minor: int | float | None) -> int:
    return int(round(float(minor or 0) / 100))


async def build_business_review(
    year: int, quarter: int, db: AsyncSession
) -> BusinessReviewResponse:
    start, end = quarter_date_range(year, quarter)
    prev_year, prev_quarter = (year - 1, 4) if quarter == 1 else (year, quarter - 1)

    current_stats = (
        await db.execute(
            select(CommunityQuarterlyStats).where(
                CommunityQuarterlyStats.year == year,
                CommunityQuarterlyStats.quarter == quarter,
            )
        )
    ).scalar_one_or_none()
    if current_stats is None:
        raise LookupError("Quarterly snapshot has not been generated for this quarter.")

    previous_stats = (
        await db.execute(
            select(CommunityQuarterlyStats).where(
                CommunityQuarterlyStats.year == prev_year,
                CommunityQuarterlyStats.quarter == prev_quarter,
            )
        )
    ).scalar_one_or_none()

    snapshot = (
        await db.execute(
            select(QuarterlySnapshot).where(
                QuarterlySnapshot.year == year,
                QuarterlySnapshot.quarter == quarter,
            )
        )
    ).scalar_one_or_none()

    date_params = {"from": start.isoformat(), "to": end.isoformat()}
    ledger_date_params = {
        "from_date": start.date().isoformat(),
        "to_date": end.date().isoformat(),
    }

    academy, club, sessions, pnl, margin, deferred, cash = await asyncio.gather(
        _safe_json(
            settings.ACADEMY_SERVICE_URL,
            "/internal/academy/quarter-summary",
            date_params,
        ),
        _safe_json(
            settings.MEMBERS_SERVICE_URL,
            "/internal/members/club-quarter-summary",
            date_params,
        ),
        _safe_json(
            settings.SESSIONS_SERVICE_URL,
            "/internal/sessions/detailed-stats",
            date_params,
        ),
        _safe_json(
            settings.LEDGER_SERVICE_URL,
            "/internal/ledger/reports/profit-loss",
            {**ledger_date_params, "group_by": "dimension_1"},
        ),
        _safe_json(
            settings.LEDGER_SERVICE_URL,
            "/internal/ledger/reports/margin",
            ledger_date_params,
        ),
        _safe_json(
            settings.LEDGER_SERVICE_URL,
            "/internal/ledger/reports/deferred-revenue",
            {"as_of": end.date().isoformat()},
        ),
        _safe_json(
            settings.LEDGER_SERVICE_URL,
            "/internal/ledger/reports/cash-position",
            {"as_of": end.date().isoformat()},
        ),
    )

    academy = academy or {}
    club = club or {}
    sessions = sessions or {}

    finance_available = pnl is not None and margin is not None
    finance = BusinessReviewFinance(
        revenue_ngn=_naira((pnl or {}).get("total_revenue_minor")),
        expenses_ngn=_naira((pnl or {}).get("total_expense_minor")),
        net_income_ngn=_naira((pnl or {}).get("net_income_minor")),
        cogs_ngn=_naira((margin or {}).get("total_cogs_minor")),
        gross_margin_ngn=_naira((margin or {}).get("total_margin_minor")),
        gross_margin_pct=(
            round(
                (
                    (margin or {}).get("total_margin_minor", 0)
                    / (margin or {}).get("total_revenue_minor", 1)
                )
                * 100,
                1,
            )
            if (margin or {}).get("total_revenue_minor")
            else 0.0
        ),
        deferred_revenue_ngn=_naira((deferred or {}).get("total_remaining_minor")),
        cash_ngn=_naira((cash or {}).get("total_minor")),
        by_domain=[
            {
                "domain": row.get("domain"),
                "revenue_ngn": _naira(row.get("revenue_minor")),
                "cogs_ngn": _naira(row.get("cogs_minor")),
                "gross_margin_ngn": _naira(row.get("margin_minor")),
                "gross_margin_pct": row.get("margin_pct", 0.0),
            }
            for row in (margin or {}).get("rows", [])
        ],
        available=finance_available,
        note=None
        if finance_available
        else "Ledger data is unavailable for this period; engagement revenue is not substituted for accounting revenue.",
    )

    location_map: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "location": "Unspecified",
            "sessions": 0,
            "scheduled_pool_hours": 0.0,
            "session_capacity": 0,
            "attendance": 0,
            "guest_attendance": 0,
            "session_types": {},
            "academy_cohorts": 0,
            "academy_active_enrollments": 0,
            "club_active_members": 0,
        }
    )
    for detail in sessions.get("session_details") or []:
        name = detail.get("location") or "Unspecified"
        row = location_map[name]
        row["location"] = name
        row["sessions"] += 1
        row["scheduled_pool_hours"] = round(
            row["scheduled_pool_hours"] + float(detail.get("hours") or 0), 2
        )
        row["session_capacity"] += int(detail.get("capacity") or 0)
        row["attendance"] += int(detail.get("attendance") or 0)
        row["guest_attendance"] += int(detail.get("guest_attendance") or 0)
        session_type = detail.get("type") or "unknown"
        row["session_types"][session_type] = (
            row["session_types"].get(session_type, 0) + 1
        )

    for name, a in (academy.get("by_location") or {}).items():
        row = location_map[name]
        row["location"] = name
        row["academy_cohorts"] = a.get("cohorts", 0)
        row["academy_active_enrollments"] = a.get("active_enrollments", 0)

    for club_name, c in (club.get("by_club") or {}).items():
        name = c.get("location") or club_name
        row = location_map[name]
        row["location"] = name
        row["club_active_members"] += c.get("active_members", 0)

    data_quality: list[str] = []
    if not finance_available:
        data_quality.append(
            "Finance section incomplete because ledger reporting was unavailable."
        )
    if not academy:
        data_quality.append("Academy quarter summary unavailable.")
    if not club:
        data_quality.append("Club quarter summary unavailable.")
    if not sessions:
        data_quality.append("Session detail summary unavailable.")
    elif not sessions.get("attendance_available"):
        data_quality.append(
            "All-human attendance detail was unavailable; community attendance falls back to the frozen member snapshot plus known GuestPass attendance."
        )
    estimated_guest_hours = float(sessions.get("estimated_guest_swimmer_hours") or 0)
    if estimated_guest_hours > 0:
        data_quality.append(
            "Some guest swimmer-hours are estimated from effective session duration because attached guests and door walk-ins do not yet store exact swim minutes. GuestPass minutes remain exact."
        )
    data_quality.append(
        "Academy completion is time-bounded by certificate issuance because Enrollment does not yet store graduated_at."
    )
    data_quality.append(
        "Club retention compares overlapping paid Club entitlements with the immediately preceding equal-length period."
    )
    data_quality.append(
        "Lead-to-sale conversion and marketing CAC are not included yet because prospect/CRM lifecycle events are not a single authoritative reporting source."
    )
    data_quality.append(
        "Ledger totals are authoritative. Historical session/guest payments created before session-type revenue snapshots may still sit in the legacy Club domain; new settlements are classified by the actual session type."
    )

    previous = previous_stats
    scorecard = {
        "active_members": _comparison(
            current_stats.total_active_members,
            previous.total_active_members if previous else None,
        ),
        "sessions_held": _comparison(
            current_stats.total_sessions_held,
            previous.total_sessions_held if previous else None,
        ),
        "attendance_rate": _comparison(
            round(current_stats.average_attendance_rate * 100, 1),
            round(previous.average_attendance_rate * 100, 1) if previous else None,
        ),
        "new_members": _comparison(
            current_stats.total_new_members,
            previous.total_new_members if previous else None,
        ),
        "milestones": _comparison(
            current_stats.total_milestones_achieved,
            previous.total_milestones_achieved if previous else None,
        ),
        "pool_hours": _comparison(
            round(current_stats.total_pool_hours, 1),
            round(previous.total_pool_hours, 1) if previous else None,
        ),
    }

    # Keep decision cards explicit and evidence-based. These are prompts, not
    # automated strategy claims.
    decisions = [
        {
            "key": "keep",
            "title": "What should we keep?",
            "prompt": "Which activity or service produced a measurable outcome worth repeating next quarter?",
        },
        {
            "key": "fix",
            "title": "What should we fix?",
            "prompt": "Which bottleneck most damaged conversion, attendance, completion, retention, or margin?",
        },
        {
            "key": "kill",
            "title": "What should we stop?",
            "prompt": "Which activity consumed time or cash without a defensible outcome?",
        },
        {
            "key": "bet",
            "title": "What is the next-quarter bet?",
            "prompt": "Choose one high-upside priority with an owner, metric, and deadline.",
        },
    ]

    return BusinessReviewResponse(
        year=year,
        quarter=quarter,
        label=quarter_label(year, quarter),
        starts_at=start,
        ends_at=end,
        snapshot_status=(
            snapshot.status.value
            if snapshot and hasattr(snapshot.status, "value")
            else str(snapshot.status)
            if snapshot
            else None
        ),
        snapshot_generated_at=snapshot.completed_at if snapshot else None,
        executive_scorecard=scorecard,
        community={
            "active_members": current_stats.total_active_members,
            "new_members": current_stats.total_new_members,
            "sessions_held": current_stats.total_sessions_held,
            "attendance_records": int(
                sessions.get("total_attendance_records")
                if sessions.get("attendance_available")
                else (
                    current_stats.total_attendance_records
                    + int(sessions.get("guest_pass_attendance_records") or 0)
                )
            ),
            "member_attendance_records": int(
                sessions.get("member_attendance_records") or 0
            ),
            "guest_attendance_records": int(
                sessions.get("guest_attendance_records") or 0
            ),
            "walk_in_guest_attendance_records": int(
                sessions.get("walk_in_guest_attendance_records") or 0
            ),
            "booking_guest_attendance_records": int(
                sessions.get("booking_guest_attendance_records") or 0
            ),
            "guest_pass_attendance_records": int(
                sessions.get("guest_pass_attendance_records") or 0
            ),
            "average_attendance_rate": current_stats.average_attendance_rate,
            "pool_hours": current_stats.total_pool_hours,
            "guest_swimmer_hours": float(sessions.get("guest_swimmer_hours") or 0),
            "exact_guest_swimmer_hours": float(
                sessions.get("exact_guest_swimmer_hours") or 0
            ),
            "estimated_guest_swimmer_hours": estimated_guest_hours,
            "milestones_achieved": current_stats.total_milestones_achieved,
            "certificates_issued": current_stats.total_certificates_issued,
            "volunteer_hours": current_stats.total_volunteer_hours,
            "rides_shared": current_stats.total_rides_shared,
            "most_active_location": current_stats.most_active_location,
            "busiest_session_title": current_stats.busiest_session_title,
            "busiest_session_attendance": current_stats.busiest_session_attendance,
            "most_popular_day": current_stats.most_popular_day,
            "most_popular_time_slot": current_stats.most_popular_time_slot,
        },
        academy=academy,
        club=club,
        finance=finance,
        locations=sorted(
            location_map.values(),
            key=lambda row: row["sessions"],
            reverse=True,
        ),
        session_mix=sessions.get("by_type") or current_stats.stats_by_type or {},
        data_quality=data_quality,
        decisions=decisions,
    )
