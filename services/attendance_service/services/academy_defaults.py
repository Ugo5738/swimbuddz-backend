"""Persist Academy's Present-by-default policy without replacing exceptions.

Academy owns enrolment eligibility, Sessions owns the timetable, Members owns
pre-swim clearance, and Attendance owns the resulting records. Consumers such
as Media, Reporting and coach payouts all read these same persisted facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import internal_get, internal_post
from libs.db.config import AsyncSessionLocal
from services.attendance_service.models import AttendanceRecord

logger = get_logger(__name__)
DEFAULT_NOTE = "Academy default Present; coach/admin exceptions override"


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def session_eligible_for_default(session: dict, *, now: datetime) -> bool:
    """Only started, included, automatic cohort classes qualify for default Present."""
    starts_at = parse_datetime(session.get("starts_at"))
    return bool(
        session.get("session_type") == "cohort_class"
        and session.get("status") in {"scheduled", "in_progress", "completed"}
        and (session.get("cohort_fee_mode") or "included") == "included"
        and (session.get("cohort_attendance_mode") or "automatic") == "automatic"
        and starts_at
        and starts_at <= now
    )


def eligible_for_default(session: dict, student: dict, *, now: datetime) -> bool:
    """Only started, included classes within an active student's enrolment."""
    if not session_eligible_for_default(session, now=now):
        return False
    starts_at = parse_datetime(session.get("starts_at"))
    joined_at = parse_datetime(student.get("enrolled_at") or student.get("created_at"))
    paused_at = parse_datetime(student.get("paused_at"))
    dropped_at = parse_datetime(student.get("dropped_at"))
    return bool(
        starts_at
        and joined_at
        and joined_at <= starts_at
        and student.get("status") == "enrolled"
        and not student.get("access_suspended", False)
        and (not paused_at or starts_at < paused_at)
        and (not dropped_at or starts_at < dropped_at)
    )


async def reconcile_session(
    db: AsyncSession,
    session: dict,
    students: list[dict],
    *,
    cleared_member_ids: set[str],
    now: datetime,
    dry_run: bool = False,
) -> dict:
    """Insert missing records atomically; every existing status wins."""
    member_ids = {
        uuid.UUID(student["member_id"])
        for student in students
        if str(student["member_id"]) in cleared_member_ids
        and eligible_for_default(session, student, now=now)
    }
    if not member_ids:
        return {"created": 0, "member_ids": []}
    session_id = uuid.UUID(session["id"])
    existing = set(
        (
            await db.scalars(
                select(AttendanceRecord.member_id).where(
                    AttendanceRecord.session_id == session_id,
                    AttendanceRecord.member_id.in_(member_ids),
                )
            )
        ).all()
    )
    missing = sorted(member_ids - existing, key=str)
    if not missing:
        return {"created": 0, "member_ids": []}
    if dry_run:
        return {"created": len(missing), "member_ids": [str(mid) for mid in missing]}
    rows = [
        {
            "id": uuid.uuid4(),
            "session_id": session_id,
            "member_id": member_id,
            "status": "present",
            "role": "swimmer",
            "notes": DEFAULT_NOTE,
        }
        for member_id in missing
    ]
    # A coach may have recorded an exception after our read. Never overwrite it.
    inserted = list(
        (
            await db.scalars(
                insert(AttendanceRecord)
                .values(rows)
                .on_conflict_do_nothing(
                    constraint="uq_session_member_attendance",
                )
                .returning(AttendanceRecord.member_id)
            )
        ).all()
    )
    return {"created": len(inserted), "member_ids": [str(mid) for mid in inserted]}


async def reconcile_academy_defaults(
    *, dry_run: bool = False, lookback_hours: int | None = None
) -> dict:
    """Repair active-cohort history or keep newly started classes up to date.

    When ``lookback_hours`` is supplied, only sessions starting within that
    trailing window are fetched and cohorts with no eligible started classes
    skip student, clearance, and DB lookups altogether. Passing
    ``lookback_hours=None`` performs a full active-cohort historical repair.
    """
    settings = get_settings()
    now = utc_now()
    session_params: dict[str, str] = {"end_date": now.isoformat()}
    if lookback_hours is not None:
        session_params["start_date"] = (
            now - timedelta(hours=max(1, lookback_hours))
        ).isoformat()
    response = await internal_get(
        service_url=settings.ACADEMY_SERVICE_URL,
        path="/internal/academy/cohorts",
        calling_service="attendance",
        params={"status": "active"},
    )
    response.raise_for_status()
    cohorts = response.json()["cohorts"]
    result = {
        "dry_run": dry_run,
        "lookback_hours": lookback_hours,
        "cohorts": len(cohorts),
        "created": 0,
        "sessions": [],
        "errors": [],
    }
    for cohort in cohorts:
        cohort_id = cohort["id"]
        try:
            sessions_response = await internal_get(
                service_url=settings.SESSIONS_SERVICE_URL,
                path=f"/internal/sessions/cohorts/{cohort_id}/sessions",
                calling_service="attendance",
                params=session_params,
            )
            sessions_response.raise_for_status()
            candidate_sessions = [
                session
                for session in sessions_response.json()
                if session_eligible_for_default(session, now=now)
            ]
            if not candidate_sessions:
                continue
            students_response = await internal_get(
                service_url=settings.ACADEMY_SERVICE_URL,
                path=f"/internal/academy/cohorts/{cohort_id}/enrolled-students",
                calling_service="attendance",
            )
            students_response.raise_for_status()
            students = students_response.json()
            if not students:
                continue
            clearance = await internal_post(
                service_url=settings.MEMBERS_SERVICE_URL,
                path="/internal/members/academy-swim-clearance/batch",
                calling_service="attendance",
                json={"member_ids": list({s["member_id"] for s in students})},
            )
            clearance.raise_for_status()
            cleared = {
                mid for mid, value in clearance.json().items() if value.get("ready")
            }
            changes = []
            async with AsyncSessionLocal() as db:
                for session in candidate_sessions:
                    change = await reconcile_session(
                        db,
                        session,
                        students,
                        cleared_member_ids=cleared,
                        now=now,
                        dry_run=dry_run,
                    )
                    if change["created"]:
                        changes.append({"session_id": session["id"], **change})
                if dry_run:
                    await db.rollback()
                else:
                    await db.commit()
            result["sessions"].extend(changes)
            result["created"] += sum(change["created"] for change in changes)
        except Exception:
            logger.exception(
                "Academy default attendance failed for cohort %s", cohort_id
            )
            result["errors"].append(cohort_id)
    logger.info(
        "Academy default attendance: created=%s errors=%s dry_run=%s lookback_hours=%s",
        result["created"],
        len(result["errors"]),
        dry_run,
        lookback_hours,
    )
    return result
