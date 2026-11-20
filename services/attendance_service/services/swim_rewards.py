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

from services.attendance_service.models import AttendanceRecord, MemberRef
from services.attendance_service.services.academy_defaults import parse_datetime

logger = get_logger(__name__)


async def eligible_swim_date(session: dict) -> date | None:
    starts = parse_datetime(session.get("starts_at"))
    if (
        not starts
        or starts > utc_now()
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
    now = utc_now()
    records = AttendanceRecord
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(
                    records.id,
                    records.session_id,
                    records.member_id,
                    MemberRef.auth_id,
                    records.swim_revision,
                    records.swim_delivered_revision,
                    records.swim_verified_on,
                    records.swim_sync_at,
                )
                .join(MemberRef, MemberRef.id == records.member_id)
                .where(records.swim_sync_at <= now)
                .order_by(
                    (records.swim_delivered_revision < records.swim_revision).desc(),
                    records.swim_sync_at,
                )
                .limit(200)
            )
        ).all()
    session_ids = list({str(row.session_id) for row in rows if row.swim_verified_on})
    sessions = {
        session["id"]: session
        for session in await get_sessions_by_ids(
            session_ids, calling_service="attendance"
        )
    }
    checked_sessions = {}
    delivered = 0
    for row in rows:
        predicate = (
            records.id == row.id,
            records.swim_revision == row.swim_revision,
            records.swim_sync_at == row.swim_sync_at,
        )
        async with AsyncSessionLocal() as db:
            try:
                if row.swim_verified_on:
                    if row.session_id not in checked_sessions:
                        checked_sessions[row.session_id] = await eligible_swim_date(
                            sessions.get(str(row.session_id), {})
                        )
                    if checked_sessions[row.session_id] != row.swim_verified_on:
                        # A cancelled or moved swim needs fresh operator confirmation.
                        await db.execute(
                            update(records)
                            .where(*predicate)
                            .values(swim_verified_on=None, swim_verified_by=None)
                        )
                        await db.commit()
                        continue
                if row.swim_delivered_revision < row.swim_revision:
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
                            if row.swim_verified_on
                            else None,
                        },
                    )
                    response.raise_for_status()
                    delivered += 1
                # Recheck session moves/cancellations even after delivery.
                next_check = now + timedelta(days=1) if row.swim_verified_on else None
                delivered_revision = row.swim_revision
            except Exception:
                await db.rollback()
                logger.exception(
                    "Swim reward reconciliation deferred for attendance %s", row.id
                )
                next_check = now + timedelta(minutes=5)
                delivered_revision = row.swim_delivered_revision
            await db.execute(
                update(records)
                .where(*predicate)
                .values(
                    swim_sync_at=next_check, swim_delivered_revision=delivered_revision
                )
            )
            await db.commit()
    return {"delivered": delivered}
