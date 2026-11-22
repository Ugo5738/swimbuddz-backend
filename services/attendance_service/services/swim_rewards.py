"""Deliver operator-confirmed swims to Wallet. A failed delivery remains due."""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import get_sessions_by_ids, internal_post
from libs.common.service_client.events import get_event_session_contract
from libs.db.config import AsyncSessionLocal
from sqlalchemy import select, update

from services.attendance_service.models import AttendanceRecord, AttendanceStatus, MemberRef
from services.attendance_service.services.academy_defaults import parse_datetime

logger = get_logger(__name__)
# Automatic rewards start now; never backfill financial awards for old
# default-Present attendance. Existing operator-verified awards are preserved.
AUTO_START = date(2026, 11, 22)
PRESENT = (AttendanceStatus.PRESENT, AttendanceStatus.LATE)


async def eligible_swim_date(session: dict) -> date | None:
    starts = parse_datetime(session.get("starts_at"))
    ends = parse_datetime(session.get("ends_at"))
    if (
        not starts
        or not ends
        or ends > utc_now()
        or session.get("status") not in {"scheduled", "in_progress", "completed"}
    ):
        return None
    if session.get("session_type") == "event" and session.get("event_id"):
        event = await get_event_session_contract(
            str(session["event_id"]), calling_service="attendance"
        )
        if (
            not event
            or event.get("event_type") != "community_swim"
            or event.get("status") in {"draft", "cancelled"}
        ):
            return None
    elif session.get("session_type") not in {"club", "community", "cohort_class"}:
        return None
    return starts.astimezone(ZoneInfo("Africa/Lagos")).date()


async def sync_due_swims():
    """Reward default Present/Late after the swim ends, reversing corrections."""
    now = utc_now()
    records = AttendanceRecord
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    records.id, records.session_id, records.member_id,
                    MemberRef.auth_id, records.status, records.swim_revision,
                    records.swim_delivered_revision, records.swim_verified_on,
                    records.swim_sync_at,
                )
                .join(MemberRef, MemberRef.id == records.member_id)
                .where(records.swim_sync_at <= now)
                .order_by(records.swim_sync_at)
                .limit(200)
            )
        ).all()
    if not rows:
        return {"delivered": 0}
    sessions = {
        str(item["id"]): item
        for item in await get_sessions_by_ids(
            list({str(r.session_id) for r in rows}), calling_service="attendance"
        )
    }
    checked: dict[str, date | None] = {}
    delivered = 0
    for row in rows:
        predicate = (
            records.id == row.id,
            records.swim_revision == row.swim_revision,
            records.swim_sync_at == row.swim_sync_at,
        )
        async with AsyncSessionLocal() as db:
            try:
                session = sessions.get(str(row.session_id), {})
                key = str(row.session_id)
                if key not in checked:
                    checked[key] = await eligible_swim_date(session)
                eligible = checked[key]
                expected = (
                    eligible
                    if row.status in PRESENT
                    and (
                        (eligible is not None and eligible >= AUTO_START)
                        or (
                            row.swim_verified_on is not None
                            and row.swim_verified_on == eligible
                        )
                    )
                    else None
                )
                if expected != row.swim_verified_on:
                    # DB trigger increments the revision and queues delivery.
                    await db.execute(
                        update(records).where(*predicate).values(
                            swim_verified_on=expected,
                            swim_verified_by="automatic_attendance" if expected else None,
                        )
                    )
                    await db.commit()
                    continue
                ends = parse_datetime(session.get("ends_at"))
                if row.status in PRESENT and not eligible and ends and ends > now:
                    next_check = ends + timedelta(minutes=1)
                else:
                    next_check = now + timedelta(days=1) if expected else None

                if row.swim_delivered_revision < row.swim_revision and (
                    row.swim_verified_on is not None
                    or row.swim_delivered_revision > 0
                ):
                    response = await internal_post(
                        service_url=get_settings().WALLET_SERVICE_URL,
                        path="/internal/wallet/verified-swims",
                        calling_service="attendance",
                        json={
                            "member_id": str(row.member_id),
                            "member_auth_id": row.auth_id,
                            "session_id": str(row.session_id),
                            "revision": row.swim_revision,
                            "swim_date": row.swim_verified_on.isoformat()
                            if row.swim_verified_on else None,
                        },
                    )
                    response.raise_for_status()
                    delivered += 1
                await db.execute(
                    update(records).where(*predicate).values(
                        swim_sync_at=next_check,
                        swim_delivered_revision=row.swim_revision,
                    )
                )
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception(
                    "Swim reward reconciliation deferred for attendance %s", row.id
                )
                await db.execute(
                    update(records).where(*predicate).values(
                        swim_sync_at=now + timedelta(minutes=5)
                    )
                )
                await db.commit()
    return {"delivered": delivered}
