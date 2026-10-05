"""Quarter-end business review composition.

The business review deliberately reuses domain-owned summaries rather than
persisting a second copy of financial, Academy, Club, or session truth.
Quarterly member/community snapshots remain the frozen engagement layer.
"""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.service_client import internal_get
from services.reporting_service.models import (
    CommunityQuarterlyStats,
    MemberQuarterlyReport,
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


def _canonical_location(name: str | None) -> tuple[str, str]:
    """Collapse known cross-service location aliases into one operating site."""
    raw = (name or "Unspecified").strip()
    lowered = raw.lower()
    aliases = (
        (("rowe park", "yaba"), "yaba", "Yaba — Rowe Park Pool"),
        (
            ("oduduwa", "victoria island"),
            "victoria-island",
            "Victoria Island — Oduduwa House Pool",
        ),
        (("siloam", "festac"), "festac", "Festac — Siloam Pool"),
        (("herel", "ikoyi"), "ikoyi", "Ikoyi — Herel Play"),
        (("ikeja",), "ikeja", "Ikeja"),
        (("ogudu",), "ogudu", "Ogudu GRA"),
    )
    for needles, key, label in aliases:
        if any(needle in lowered for needle in needles):
            return key, label
    return lowered or "unspecified", raw or "Unspecified"


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

    previous_snapshot = (
        await db.execute(
            select(QuarterlySnapshot).where(
                QuarterlySnapshot.year == prev_year,
                QuarterlySnapshot.quarter == prev_quarter,
            )
        )
    ).scalar_one_or_none()

    comparison_compatible = bool(
        snapshot
        and previous_snapshot
        and snapshot.semantics_version >= 2
        and snapshot.semantics_version == previous_snapshot.semantics_version
    )

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
    revenue_minor = int((margin or {}).get("total_revenue_minor") or 0)
    cogs_minor = int((margin or {}).get("total_cogs_minor") or 0)
    profitability_reliable = bool(
        finance_available and (revenue_minor == 0 or cogs_minor > 0)
    )
    finance = BusinessReviewFinance(
        revenue_ngn=_naira((pnl or {}).get("total_revenue_minor")),
        expenses_ngn=_naira((pnl or {}).get("total_expense_minor")),
        net_income_ngn=_naira((pnl or {}).get("net_income_minor")),
        cogs_ngn=_naira((margin or {}).get("total_cogs_minor")),
        gross_margin_ngn=_naira((margin or {}).get("total_margin_minor")),
        profitability_reliable=profitability_reliable,
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
        note=(
            None
            if profitability_reliable
            else (
                "Ledger revenue is available, but direct costs/COGS are not sufficiently classified for a reliable gross-margin or net-income conclusion."
                if finance_available
                else "Ledger data is unavailable for this period; engagement revenue is not substituted for accounting revenue."
            )
        ),
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
        key, label = _canonical_location(detail.get("location"))
        row = location_map[key]
        row["location"] = label
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
        key, label = _canonical_location(name)
        row = location_map[key]
        row["location"] = label
        row["academy_cohorts"] = a.get("cohorts", 0)
        row["academy_active_enrollments"] = a.get("active_enrollments", 0)

    for club_name, c in (club.get("by_club") or {}).items():
        key, label = _canonical_location(c.get("location") or club_name)
        row = location_map[key]
        row["location"] = label
        row["club_active_members"] += c.get("active_members", 0)

    report_names = list(
        (
            await db.execute(
                select(MemberQuarterlyReport.member_name).where(
                    MemberQuarterlyReport.year == year,
                    MemberQuarterlyReport.quarter == quarter,
                )
            )
        )
        .scalars()
        .all()
    )
    normalized_name_counts = Counter(
        name.strip().lower() for name in report_names if name
    )
    duplicate_names = sorted(
        name for name, count in normalized_name_counts.items() if count > 1
    )

    data_quality: list[str] = []
    if not finance_available:
        data_quality.append(
            "Finance section incomplete because ledger reporting was unavailable."
        )
    elif not profitability_reliable:
        data_quality.append(
            "Revenue is ledger-backed, but Q3 direct costs/COGS are not fully classified; gross margin and net income must not be treated as profitability conclusions."
        )
    if not academy:
        data_quality.append("Academy quarter summary unavailable.")
    elif (
        int(academy.get("active_enrollments") or 0) > 0
        and int(academy.get("progress_updates_in_period") or 0) == 0
    ):
        data_quality.append(
            "Academy had active learners but no progress records were updated in the quarter; zero milestones/certificates should be treated as incomplete progress-recording coverage, not evidence of zero learner progress."
        )
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
        "Academy new-enrollment timing uses enrolled_at, then paid_at, then created_at as a legacy fallback; completion is time-bounded by certificate issuance because Enrollment does not yet store graduated_at."
    )
    if club and not club.get("retention_available", True):
        data_quality.append(
            club.get("retention_note")
            or "Club retention is unavailable because the historical entitlement baseline is incomplete."
        )
    else:
        data_quality.append(
            "Club retention compares overlapping paid Club entitlements with the immediately preceding equal-length period."
        )
    data_quality.append(
        "Guest/walk-in history is complete only where canonical GuestPass/participant attendance was recorded; older manually handled guests may be absent."
    )
    data_quality.append(
        "Lead-to-sale conversion and marketing CAC are not included yet because prospect/CRM lifecycle events are not a single authoritative reporting source."
    )
    data_quality.append(
        "Known location aliases are normalized for management reporting (for example Yaba/Rowe Park, VI/Oduduwa House, Festac/Siloam and Ikoyi/Herel Play)."
    )
    if duplicate_names:
        data_quality.append(
            "Possible duplicate swimmer identities share the same display name: "
            + ", ".join(duplicate_names)
            + ". Review these accounts before member-facing distribution."
        )
    if previous_stats is not None and not comparison_compatible:
        data_quality.append(
            f"Q{prev_quarter} {prev_year} was generated under an older reporting definition, so QoQ comparisons are hidden until that quarter is regenerated."
        )
    data_quality.append(
        "Ledger totals are authoritative. Historical session/guest payments created before session-type revenue snapshots may still sit in the legacy Club domain; new settlements are classified by the actual session type."
    )

    distribution_blockers: list[str] = []
    if not snapshot or snapshot.semantics_version < 2:
        distribution_blockers.append(
            "Regenerate this quarter under the corrected reporting definitions before sending member reports."
        )
    if duplicate_names:
        distribution_blockers.append(
            "Review possible duplicate swimmer identities before member-facing distribution."
        )

    previous = previous_stats if comparison_compatible else None
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
            "registered_swimmer_profiles": snapshot.member_count if snapshot else 0,
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
        member_distribution_ready=not distribution_blockers,
        member_distribution_blockers=distribution_blockers,
        decisions=decisions,
    )
