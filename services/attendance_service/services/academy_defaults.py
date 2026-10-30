"""Persist Academy's Present-by-default policy without replacing exceptions.

Academy owns enrolment eligibility, Sessions owns the timetable, Members owns
pre-swim clearance, and Attendance owns the resulting records. Consumers such
as Media, Reporting and coach payouts all read these same persisted facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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


def eligible_for_default(session: dict, student: dict, *, now: datetime) -> bool:
    """Only started, included classes within an active student's enrolment."""
    starts_at = parse_datetime(session.get("starts_at"))
    joined_at = parse_datetime(student.get("enrolled_at") or student.get("created_at"))
    paused_at = parse_datetime(student.get("paused_at"))
    dropped_at = parse_datetime(student.get("dropped_at"))
    return bool(
        session.get("session_type") == "cohort_class"
        and session.get("status") in {"scheduled", "in_progress", "completed"}
        and (session.get("cohort_fee_mode") or "included") == "included"
        and (session.get("cohort_attendance_mode") or "automatic") == "automatic"
        and starts_at
        and starts_at <= now
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


async def reconcile_academy_defaults(*, dry_run: bool = False) -> dict:
    """Repair active-cohort history and keep newly started classes up to date."""
    settings = get_settings()
    now = utc_now()
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
        "cohorts": len(cohorts),
        "created": 0,
        "sessions": [],
        "errors": [],
    }
    for cohort in cohorts:
        cohort_id = cohort["id"]
        try:
            students_response = await internal_get(
                service_url=settings.ACADEMY_SERVICE_URL,
                path=f"/internal/academy/cohorts/{cohort_id}/enrolled-students",
                calling_service="attendance",
            )
            students_response.raise_for_status()
            students = students_response.json()
            if not students:
                continue
            sessions_response = await internal_get(
                service_url=settings.SESSIONS_SERVICE_URL,
                path=f"/internal/sessions/cohorts/{cohort_id}/sessions",
                calling_service="attendance",
                params={"end_date": now.isoformat()},
            )
            sessions_response.raise_for_status()
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
                for session in sessions_response.json():
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
        "Academy default attendance: created=%s errors=%s dry_run=%s",
        result["created"],
        len(result["errors"]),
        dry_run,
    )
    return result
