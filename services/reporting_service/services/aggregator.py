"""Cross-service data aggregation for quarterly reports.

Fetches data from attendance, sessions, payments, wallet, academy, store,
volunteer, and transport services to compute member and community reports.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from services.reporting_service.models import (
    CommunityQuarterlyStats,
    MemberQuarterlyReport,
)
from services.reporting_service.services.quarter_utils import quarter_date_range

logger = get_logger(__name__)
settings = get_settings()

CALLING_SERVICE = "reporting"
# Limit concurrent service calls to avoid overwhelming other services
MAX_CONCURRENT = 10
ATTENDED_STATUSES = {"present", "late"}
EXCUSED_STATUSES = {"excused", "cancelled"}
REPORTABLE_SESSION_TYPES = {"cohort_class", "club", "community", "event"}


async def _safe_get(
    service_url: str, path: str, params: dict | None = None
) -> dict | list | None:
    """Make an internal GET call and return JSON, or None on failure."""
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
        logger.warning(f"Service call {path} returned {resp.status_code}")
    except Exception as e:
        logger.warning(f"Service call {path} failed: {e}")
    return None


async def _fetch_member_info(member_auth_id: str) -> dict | None:
    """Fetch member profile info from members service."""
    return await _safe_get(
        settings.MEMBERS_SERVICE_URL,
        f"/internal/members/by-auth/{member_auth_id}",
    )


async def _fetch_attendance_stats(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch attendance aggregation from attendance service."""
    data = await _safe_get(
        settings.ATTENDANCE_SERVICE_URL,
        f"/internal/attendance/stats/member/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_member_attendance_records(
    member_id: str | None, session_ids: list[str]
) -> list[dict]:
    """Fetch raw attendance records for a member over a known session set."""
    if not member_id or not session_ids:
        return []
    data = await _safe_get(
        settings.ATTENDANCE_SERVICE_URL,
        f"/internal/attendance/member/{member_id}",
        params={"session_ids": ",".join(session_ids)},
    )
    return data if isinstance(data, list) else []


async def _fetch_session_stats(date_from: str, date_to: str) -> dict:
    """Fetch session counts in range from sessions service."""
    data = await _safe_get(
        settings.SESSIONS_SERVICE_URL,
        "/internal/sessions/range-stats",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_session_detailed_stats(date_from: str, date_to: str) -> dict:
    """Fetch detailed session stats for quarterly reports."""
    data = await _safe_get(
        settings.SESSIONS_SERVICE_URL,
        "/internal/sessions/detailed-stats",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_member_session_commitments(
    member_auth_id: str, date_from: str, date_to: str
) -> list[dict]:
    """Fetch confirmed session commitments in the reporting window."""
    data = await _safe_get(
        settings.SESSIONS_SERVICE_URL,
        f"/internal/sessions/member/{member_auth_id}/session-commitments",
        params={"from": date_from, "to": date_to},
    )
    return data if isinstance(data, list) else []


async def _fetch_cohort_sessions(
    cohort_id: str, date_from: str, date_to: str
) -> list[dict]:
    """Fetch in-window sessions for an academy cohort."""
    data = await _safe_get(
        settings.SESSIONS_SERVICE_URL,
        f"/internal/sessions/cohorts/{cohort_id}/sessions",
        params={
            "start_date": date_from,
            "end_date": date_to,
            "statuses": "scheduled,in_progress,completed",
        },
    )
    return data if isinstance(data, list) else []


async def _fetch_payment_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch payment summary from payments service."""
    data = await _safe_get(
        settings.PAYMENTS_SERVICE_URL,
        f"/internal/payments/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_wallet_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch wallet (bubbles) summary from wallet service."""
    data = await _safe_get(
        settings.WALLET_SERVICE_URL,
        f"/internal/wallet/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_academy_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch academy progress summary from academy service."""
    data = await _safe_get(
        settings.ACADEMY_SERVICE_URL,
        f"/internal/academy/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_member_cohort_enrollments(
    member_auth_id: str, date_from: str, date_to: str
) -> list[dict]:
    """Fetch academy cohort enrollments overlapping the reporting window."""
    data = await _safe_get(
        settings.ACADEMY_SERVICE_URL,
        f"/internal/academy/member-cohort-enrollments/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data if isinstance(data, list) else []


async def _fetch_store_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch store order summary from store service."""
    data = await _safe_get(
        settings.STORE_SERVICE_URL,
        f"/internal/store/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_volunteer_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch volunteer hours from volunteer service."""
    data = await _safe_get(
        settings.VOLUNTEER_SERVICE_URL,
        f"/internal/volunteer/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_transport_summary(
    member_auth_id: str, date_from: str, date_to: str
) -> dict:
    """Fetch transport/ride-share stats from transport service."""
    data = await _safe_get(
        settings.TRANSPORT_SERVICE_URL,
        f"/internal/transport/member-summary/{member_auth_id}",
        params={"from": date_from, "to": date_to},
    )
    return data or {}


async def _fetch_all_approved_members() -> list[dict]:
    """Fetch list of all approved members from members service."""
    data = await _safe_get(
        settings.MEMBERS_SERVICE_URL,
        "/internal/members/approved-list",
    )
    return data or []


def _compute_streaks(attendance_data: dict) -> tuple[int, int]:
    """Compute longest and current weekly attendance streaks.

    Expects attendance_data to contain 'weekly_attendance' — a list of booleans
    indicating whether the member attended at least one session each week.
    """
    weeks = attendance_data.get("weekly_attendance", [])
    if not weeks:
        return 0, 0

    longest = 0
    current = 0

    for attended in weeks:
        if attended:
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    return longest, current


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _session_type(session: dict) -> str:
    raw = session.get("session_type") or ""
    return raw.value if hasattr(raw, "value") else str(raw)


def _session_id(session: dict) -> str | None:
    value = session.get("session_id") or session.get("id")
    return str(value) if value else None


def _is_enrollment_active_for_session(enrollment: dict, session: dict) -> bool:
    starts_at = _parse_iso_datetime(session.get("starts_at"))
    if starts_at is None:
        return False

    enrolled_at = _parse_iso_datetime(enrollment.get("enrolled_at"))
    if enrolled_at and starts_at < enrolled_at:
        return False

    dropped_at = _parse_iso_datetime(enrollment.get("dropped_at"))
    if dropped_at and starts_at > dropped_at:
        return False

    paused_at = _parse_iso_datetime(enrollment.get("paused_at"))
    if paused_at and starts_at >= paused_at:
        return False

    return True


def _attendance_status_by_session(records: list[dict]) -> dict[str, str]:
    """Collapse possibly duplicated rows to one outcome per session."""
    statuses: dict[str, str] = {}
    for record in records:
        session_id = str(record.get("session_id") or "")
        status = str(record.get("status") or "").lower()
        if not session_id or not status:
            continue
        existing = statuses.get(session_id)
        if status in ATTENDED_STATUSES:
            statuses[session_id] = status
        elif existing not in ATTENDED_STATUSES and status == "absent":
            statuses[session_id] = status
        elif existing is None:
            statuses[session_id] = status
    return statuses


def _effective_pool_hours(session: dict) -> float:
    starts_at = _parse_iso_datetime(session.get("starts_at"))
    ends_at = _parse_iso_datetime(session.get("ends_at"))
    if starts_at is None or ends_at is None:
        return 0.0
    raw_hours = max(0.0, (ends_at - starts_at).total_seconds() / 3600)
    return max(0.0, raw_hours - 1.0)


def _weekly_attendance(
    *, start: datetime, end: datetime, attended_sessions: list[dict]
) -> list[bool]:
    attended_weeks = set()
    for session in attended_sessions:
        starts_at = _parse_iso_datetime(session.get("starts_at"))
        if starts_at is not None:
            iso = starts_at.isocalendar()
            attended_weeks.add((iso.year, iso.week))

    weeks: list[bool] = []
    current = start
    while current <= end:
        iso = current.isocalendar()
        weeks.append((iso.year, iso.week) in attended_weeks)
        current += timedelta(weeks=1)
    return weeks


def _summarize_expected_sessions(
    *, expected_sessions: dict[str, dict], status_by_session: dict[str, str]
) -> dict:
    attended_sessions: list[dict] = []
    counted = 0
    attended = 0
    absent = 0
    excused = 0
    on_time = 0

    day_counts: Counter = Counter()
    location_counts: Counter = Counter()

    for session_id, session in expected_sessions.items():
        status = status_by_session.get(session_id)
        if status in EXCUSED_STATUSES:
            excused += 1
            continue

        counted += 1
        if status in ATTENDED_STATUSES:
            attended += 1
            attended_sessions.append(session)
            if status == "present":
                on_time += 1
            starts_at = _parse_iso_datetime(session.get("starts_at"))
            if starts_at is not None:
                day_counts[starts_at.strftime("%A")] += 1
            if session.get("location_name"):
                location_counts[session["location_name"]] += 1
        else:
            absent += 1

    return {
        "total_attended": attended,
        "total_available": counted,
        "attendance_rate": attended / counted if counted > 0 else 0.0,
        "punctuality_rate": on_time / attended if attended > 0 else 0.0,
        "total_absent": absent,
        "total_excused": excused,
        "attended_sessions": attended_sessions,
        "favorite_day": day_counts.most_common(1)[0][0] if day_counts else None,
        "favorite_location": (
            location_counts.most_common(1)[0][0] if location_counts else None
        ),
    }


async def _compute_segment_attendance(
    *,
    member_auth_id: str,
    member_id: str | None,
    start: datetime,
    end: datetime,
    fallback_attendance: dict,
) -> dict:
    """Compute report attendance by primary segment.

    Academy wins over Club for mixed members because cohort enrollment is the
    strongest commitment. Community-only members retain non-attendance card
    metrics; their generic report attendance falls back to existing stats.
    """
    elapsed_end = min(end, utc_now())
    date_from = start.isoformat()
    date_to = elapsed_end.isoformat()

    commitments, enrollments = await asyncio.gather(
        _fetch_member_session_commitments(member_auth_id, date_from, date_to),
        _fetch_member_cohort_enrollments(member_auth_id, date_from, date_to),
    )

    expected_by_segment: dict[str, dict[str, dict]] = {
        "academy": {},
        "club": {},
        "community": {},
    }

    for commitment in commitments:
        sid = _session_id(commitment)
        if not sid:
            continue
        stype = _session_type(commitment)
        if stype == "club":
            expected_by_segment["club"][sid] = commitment
        elif stype in {"community", "event"}:
            expected_by_segment["community"][sid] = commitment
        elif stype == "cohort_class":
            expected_by_segment["academy"][sid] = commitment

    cohort_ids = sorted({e.get("cohort_id") for e in enrollments if e.get("cohort_id")})
    if cohort_ids:
        cohort_session_lists = await asyncio.gather(
            *[_fetch_cohort_sessions(cid, date_from, date_to) for cid in cohort_ids]
        )
        sessions_by_cohort = dict(zip(cohort_ids, cohort_session_lists))
        for enrollment in enrollments:
            cohort_id = enrollment.get("cohort_id")
            if not cohort_id:
                continue
            for session in sessions_by_cohort.get(cohort_id, []):
                sid = _session_id(session)
                if sid and _is_enrollment_active_for_session(enrollment, session):
                    expected_by_segment["academy"][sid] = session

    all_expected = {
        sid: session
        for segment_sessions in expected_by_segment.values()
        for sid, session in segment_sessions.items()
    }

    records = await _fetch_member_attendance_records(
        member_id, sorted(all_expected.keys())
    )
    status_by_session = _attendance_status_by_session(records)

    summaries = {
        segment: _summarize_expected_sessions(
            expected_sessions=sessions, status_by_session=status_by_session
        )
        for segment, sessions in expected_by_segment.items()
    }

    if summaries["academy"]["total_available"] > 0:
        primary_segment = "academy"
    elif summaries["club"]["total_available"] > 0:
        primary_segment = "club"
    else:
        primary_segment = "community"

    primary = summaries[primary_segment]
    used_fallback_attendance = False
    if primary["total_available"] == 0:
        used_fallback_attendance = True
        total_attended = fallback_attendance.get(
            "total_present", 0
        ) + fallback_attendance.get("total_late", 0)
        total_available = fallback_attendance.get("total_sessions", 0)
        primary.update(
            {
                "total_attended": total_attended,
                "total_available": total_available,
                "attendance_rate": (
                    total_attended / total_available if total_available > 0 else 0.0
                ),
                "punctuality_rate": (
                    fallback_attendance.get("total_present", 0) / total_attended
                    if total_attended > 0
                    else 0.0
                ),
                "favorite_day": fallback_attendance.get("favorite_day"),
                "favorite_location": fallback_attendance.get("favorite_location"),
            }
        )

    attended_sessions = [
        session
        for summary in summaries.values()
        for session in summary.get("attended_sessions", [])
    ]
    computed_pool_hours = round(
        sum(_effective_pool_hours(s) for s in attended_sessions), 1
    )
    fallback_pool_hours = float(fallback_attendance.get("total_pool_hours", 0.0) or 0.0)

    weekly_attendance = []
    if not used_fallback_attendance:
        weekly_attendance = _weekly_attendance(
            start=start,
            end=elapsed_end,
            attended_sessions=primary.get("attended_sessions", []),
        )
    by_type = {
        session_type: count
        for session_type, count in Counter(
            _session_type(session)
            for session in all_expected.values()
            if _session_type(session) in REPORTABLE_SESSION_TYPES
        ).items()
    }

    return {
        "primary_segment": primary_segment,
        "total_attended": primary["total_attended"],
        "total_available": primary["total_available"],
        "attendance_rate": primary["attendance_rate"],
        "punctuality_rate": primary["punctuality_rate"],
        "favorite_day": primary["favorite_day"],
        "favorite_location": primary["favorite_location"],
        "weekly_attendance": weekly_attendance
        or fallback_attendance.get("weekly_attendance", []),
        "pool_hours": computed_pool_hours or fallback_pool_hours,
        "sessions_by_type": by_type or fallback_attendance.get("by_type"),
    }


async def compute_member_report(
    *,
    member_auth_id: str,
    year: int,
    quarter: int,
    db: AsyncSession,
    member_info: dict | None = None,
) -> MemberQuarterlyReport:
    """Compute or update a single member's quarterly report.

    Fetches data from all services in parallel and assembles the report.
    """
    start, end = quarter_date_range(year, quarter)
    date_from = start.isoformat()
    date_to = end.isoformat()

    # Fetch all data in parallel
    (
        fetched_member,
        attendance,
        payments,
        wallet,
        academy,
        store,
        volunteer,
        transport,
    ) = await asyncio.gather(
        _fetch_member_info(member_auth_id)
        if member_info is None
        else _noop(member_info),
        _fetch_attendance_stats(member_auth_id, date_from, date_to),
        _fetch_payment_summary(member_auth_id, date_from, date_to),
        _fetch_wallet_summary(member_auth_id, date_from, date_to),
        _fetch_academy_summary(member_auth_id, date_from, date_to),
        _fetch_store_summary(member_auth_id, date_from, date_to),
        _fetch_volunteer_summary(member_auth_id, date_from, date_to),
        _fetch_transport_summary(member_auth_id, date_from, date_to),
    )

    if member_info is None:
        member_info = fetched_member or {}

    member_name = (
        f"{member_info.get('first_name', '')} {member_info.get('last_name', '')}"
    ).strip() or "Unknown Member"
    member_id = member_info.get("id")
    member_tier = member_info.get("primary_tier")

    # Detect first-timer (joined this quarter)
    member_created_at = member_info.get("created_at") or member_info.get("approved_at")
    is_first_quarter = False
    if member_created_at:
        from datetime import datetime as _dt

        try:
            joined = _dt.fromisoformat(member_created_at.replace("Z", "+00:00"))
            if start <= joined <= end:
                is_first_quarter = True
        except Exception:
            pass

    segment_attendance = await _compute_segment_attendance(
        member_auth_id=member_auth_id,
        member_id=member_id,
        start=start,
        end=end,
        fallback_attendance=attendance,
    )

    # Compute derived attendance metrics
    total_attended = segment_attendance["total_attended"]
    total_available = segment_attendance["total_available"]
    attendance_rate = segment_attendance["attendance_rate"]
    punctuality_rate = segment_attendance["punctuality_rate"]
    streak_longest, streak_current = _compute_streaks(segment_attendance)

    # Check for existing report
    result = await db.execute(
        select(MemberQuarterlyReport).where(
            MemberQuarterlyReport.member_auth_id == member_auth_id,
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    report = result.scalar_one_or_none()

    report_data = dict(
        member_id=member_id,
        member_auth_id=member_auth_id,
        year=year,
        quarter=quarter,
        member_name=member_name,
        member_tier=member_tier,
        # Attendance
        total_sessions_attended=total_attended,
        total_sessions_available=total_available,
        attendance_rate=attendance_rate,
        sessions_by_type=segment_attendance.get("sessions_by_type"),
        punctuality_rate=punctuality_rate,
        streak_longest=streak_longest,
        streak_current=streak_current,
        favorite_day=segment_attendance.get("favorite_day"),
        favorite_location=segment_attendance.get("favorite_location"),
        # Academy
        milestones_achieved=academy.get("milestones_achieved", 0),
        milestones_in_progress=academy.get("milestones_in_progress", 0),
        programs_enrolled=academy.get("programs_enrolled", 0),
        certificates_earned=academy.get("certificates_earned", 0),
        # Financial
        total_spent_ngn=payments.get("total_spent", 0),
        bubbles_earned=wallet.get("bubbles_earned", 0),
        bubbles_spent=wallet.get("bubbles_spent", 0),
        # Transport
        rides_taken=transport.get("rides_taken", 0),
        rides_offered=transport.get("rides_offered", 0),
        # Volunteer
        volunteer_hours=volunteer.get("total_hours", 0.0),
        # Store
        orders_placed=store.get("orders_placed", 0),
        store_spent_ngn=store.get("total_spent", 0),
        # Events
        events_attended=attendance.get("events_attended", 0),
        # Pool hours (computed from attended session durations)
        pool_hours=segment_attendance.get("pool_hours", 0.0),
        # First-timer
        is_first_quarter=is_first_quarter,
        member_joined_at=member_created_at,
        # Academy detail
        academy_skills=academy.get("skills_unlocked"),
        cohorts_completed=academy.get("cohorts_completed", 0),
        # Percentile will be computed after all members are processed
        attendance_percentile=0.0,
        # Timestamps
        computed_at=utc_now(),
    )

    if report:
        for key, value in report_data.items():
            if key not in ("member_id", "member_auth_id", "year", "quarter"):
                setattr(report, key, value)
    else:
        report = MemberQuarterlyReport(**report_data)
        db.add(report)

    await db.commit()
    await db.refresh(report)
    return report


async def compute_all_member_reports(year: int, quarter: int, db: AsyncSession) -> int:
    """Compute reports for all approved members. Returns count of reports generated.

    Processes members sequentially to avoid SQLAlchemy async session
    concurrency issues (a single AsyncSession must not be used by
    concurrent coroutines).
    """
    members = await _fetch_all_approved_members()
    if not members:
        logger.warning("No approved members found to generate reports for.")
        return 0

    count = 0

    for member in members:
        try:
            await compute_member_report(
                member_auth_id=member["auth_id"],
                year=year,
                quarter=quarter,
                db=db,
                member_info=member,
            )
            count += 1
        except Exception as e:
            logger.error(f"Failed to compute report for {member.get('auth_id')}: {e}")
            # Rollback so the session is usable for the next member
            await db.rollback()

    logger.info(f"Generated {count} member reports for Q{quarter} {year}")

    # Compute percentile ranks across all member reports
    await _compute_percentile_ranks(year, quarter, db)

    return count


async def _compute_percentile_ranks(year: int, quarter: int, db: AsyncSession) -> None:
    """Compute attendance percentile for each member report.

    A percentile of 0.8 means "you're in the top 20%".
    """
    result = await db.execute(
        select(MemberQuarterlyReport)
        .where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
        .order_by(MemberQuarterlyReport.total_sessions_attended.asc())
    )
    reports = result.scalars().all()
    total = len(reports)

    if total == 0:
        return

    for i, report in enumerate(reports):
        # Percentile: fraction of members with fewer or equal sessions
        report.attendance_percentile = round((i + 1) / total, 2)

    await db.commit()
    logger.info(f"Computed percentile ranks for {total} members Q{quarter} {year}")


async def compute_community_stats(
    year: int, quarter: int, db: AsyncSession
) -> CommunityQuarterlyStats:
    """Compute community-wide stats from individual member reports."""
    # Aggregate from member reports already in DB
    result = await db.execute(
        select(
            func.count(MemberQuarterlyReport.id).label("total_members"),
            func.sum(MemberQuarterlyReport.total_sessions_attended).label(
                "total_attendance"
            ),
            func.avg(MemberQuarterlyReport.attendance_rate).label("avg_rate"),
            func.sum(MemberQuarterlyReport.milestones_achieved).label(
                "total_milestones"
            ),
            func.sum(MemberQuarterlyReport.certificates_earned).label("total_certs"),
            func.sum(MemberQuarterlyReport.volunteer_hours).label("total_volunteer"),
            func.sum(MemberQuarterlyReport.rides_taken).label("total_rides"),
            func.sum(MemberQuarterlyReport.total_spent_ngn).label("total_revenue"),
            func.sum(MemberQuarterlyReport.pool_hours).label("total_pool_hours_member"),
            func.sum(MemberQuarterlyReport.cohorts_completed).label("total_cohorts"),
        ).where(
            MemberQuarterlyReport.year == year,
            MemberQuarterlyReport.quarter == quarter,
        )
    )
    row = result.one()
    row_cohorts = row.total_cohorts

    # Fetch session stats for the quarter
    start, end = quarter_date_range(year, quarter)
    session_stats = await _fetch_session_stats(start.isoformat(), end.isoformat())
    detailed_stats = await _fetch_session_detailed_stats(
        start.isoformat(), end.isoformat()
    )

    # Check for existing stats
    result = await db.execute(
        select(CommunityQuarterlyStats).where(
            CommunityQuarterlyStats.year == year,
            CommunityQuarterlyStats.quarter == quarter,
        )
    )
    stats = result.scalar_one_or_none()

    stats_data = dict(
        year=year,
        quarter=quarter,
        total_active_members=row.total_members or 0,
        total_sessions_held=session_stats.get("total_sessions", 0),
        total_attendance_records=row.total_attendance or 0,
        average_attendance_rate=float(row.avg_rate or 0.0),
        total_new_members=session_stats.get("new_members", 0),
        total_milestones_achieved=row.total_milestones or 0,
        total_certificates_issued=row.total_certs or 0,
        total_volunteer_hours=float(row.total_volunteer or 0.0),
        total_rides_shared=row.total_rides or 0,
        total_revenue_ngn=row.total_revenue or 0,
        # Pool hours
        total_pool_hours=detailed_stats.get("total_pool_hours", 0.0),
        # Location & session highlights
        most_active_location=detailed_stats.get("most_active_location"),
        busiest_session_title=detailed_stats.get("busiest_session_title"),
        busiest_session_attendance=detailed_stats.get("busiest_session_attendance", 0),
        most_popular_day=detailed_stats.get("most_popular_day"),
        most_popular_time_slot=detailed_stats.get("most_popular_time_slot"),
        # Academy
        total_cohorts_completed=row_cohorts or 0,
        stats_by_type=session_stats.get("by_type"),
        # Community milestones
        community_milestones=_build_community_milestones(
            total_pool_hours=detailed_stats.get("total_pool_hours", 0.0),
            total_members=row.total_members or 0,
            total_sessions=session_stats.get("total_sessions", 0),
            total_volunteer=float(row.total_volunteer or 0.0),
        ),
        computed_at=utc_now(),
    )

    if stats:
        for key, value in stats_data.items():
            if key not in ("year", "quarter"):
                setattr(stats, key, value)
    else:
        stats = CommunityQuarterlyStats(**stats_data)
        db.add(stats)

    await db.commit()
    await db.refresh(stats)
    return stats


def _build_community_milestones(
    total_pool_hours: float,
    total_members: int,
    total_sessions: int,
    total_volunteer: float,
) -> list[dict]:
    """Build fun community milestone comparisons (Lagos Lagoon style)."""
    milestones = []

    # Lagos Lagoon is roughly 6.4 km across. Average swim speed ~2 km/h.
    # So crossing takes ~3.2 hours.
    if total_pool_hours > 0:
        lagoon_crossings = total_pool_hours / 3.2
        milestones.append(
            {
                "icon": "waves",
                "text": f"Together we spent {total_pool_hours:.0f} hours in the pool — "
                f"that's like crossing the Lagos Lagoon {lagoon_crossings:.0f} times!",
            }
        )

    # Olympic pool is 50m. Average swim speed in training ~1.5 km/h
    # In total_pool_hours we could swim total_pool_hours * 1500 metres
    if total_pool_hours > 0:
        km_swum = total_pool_hours * 1.5
        milestones.append(
            {
                "icon": "map",
                "text": f"We collectively swam an estimated {km_swum:.0f} km — "
                f"that's the distance from Lagos to {'Ibadan' if km_swum < 150 else 'Abuja' if km_swum < 800 else 'Cape Town'}!",
            }
        )

    if total_sessions > 0:
        milestones.append(
            {
                "icon": "calendar",
                "text": f"We held {total_sessions} sessions this quarter — "
                f"that's about {total_sessions / 13:.1f} sessions per week!",
            }
        )

    if total_members > 0:
        milestones.append(
            {
                "icon": "users",
                "text": f"{total_members} active swimmers made waves together this quarter.",
            }
        )

    if total_volunteer > 0:
        milestones.append(
            {
                "icon": "heart",
                "text": f"Our volunteers gave {total_volunteer:.0f} hours back to the community!",
            }
        )

    return milestones


async def _noop(value: Any) -> Any:
    """Async no-op that returns the given value. Used for asyncio.gather."""
    return value
