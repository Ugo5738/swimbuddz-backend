"""Service-owned Club schedule and costing snapshots, without cross-service SQL."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.common.config import get_settings
from libs.common.currency import naira_to_kobo
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.sessions_service.models import (
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
)
from services.sessions_service.services.pricing import (
    normalize_pricing_payload,
    pricing_payload_from_session,
)

router = APIRouter(
    prefix="/internal/sessions/club-schedule",
    tags=["internal-club-schedule"],
    dependencies=[Depends(require_service_role)],
)


class ScheduleQuery(BaseModel):
    session_ids: list[uuid.UUID] = Field(default_factory=list, max_length=260)
    pool_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None


def session_snapshot(session: Session) -> dict:
    return {
        "id": str(session.id),
        "title": session.title,
        "pool_id": str(session.pool_id) if session.pool_id else None,
        "pod_id": str(session.pod_id) if session.pod_id else None,
        "session_type": session.session_type.value,
        "status": session.status.value,
        "starts_at": session.starts_at.isoformat(),
        "ends_at": session.ends_at.isoformat(),
        "timezone": session.timezone,
        "capacity": session.capacity,
        "fee_kobo": session.pool_fee,
        "pricing": {
            "mode": session.pricing_mode,
            "cost_lines": session.cost_lines or [],
            "cost_per_attendee_kobo": session.estimated_cost_per_attendee,
            "margin_per_attendee_kobo": session.margin_amount_per_attendee,
            "expected_attendees": session.pricing_expected_attendees,
        },
    }


@router.post("/query")
async def query_schedule(body: ScheduleQuery, db: AsyncSession = Depends(get_async_db)):
    if not body.session_ids and not (
        body.pool_id and body.period_start and body.period_end
    ):
        raise HTTPException(422, "Supply session IDs or a pool and date range")
    query = select(Session)
    if body.session_ids:
        query = query.where(Session.id.in_(body.session_ids))
    else:
        tz = ZoneInfo("Africa/Lagos")
        query = query.where(
            Session.pool_id == body.pool_id,
            Session.session_type == SessionType.CLUB,
            Session.starts_at
            >= datetime.combine(body.period_start, time.min, tzinfo=tz),
            Session.starts_at
            < datetime.combine(
                body.period_end + timedelta(days=1), time.min, tzinfo=tz
            ),
        )
    return [
        session_snapshot(row)
        for row in (await db.execute(query.order_by(Session.starts_at))).scalars()
    ]


class GenerateQuarter(BaseModel):
    club_id: uuid.UUID
    source_session_id: uuid.UUID
    pool_id: uuid.UUID
    period_start: date
    period_end: date
    weekday: int = Field(ge=0, le=6)
    starts_at_local: time
    duration_minutes: int = Field(ge=15, le=480)
    expected_staff: int = Field(default=0, ge=0, le=50)
    lanes: int = Field(default=1, ge=1, le=50)

    @model_validator(mode="after")
    def valid_range(self):
        if not 0 <= (self.period_end - self.period_start).days <= 100:
            raise ValueError("Generate at most one quarter at a time")
        return self


@router.post("/generate")
async def generate_quarter(
    body: GenerateQuarter, db: AsyncSession = Depends(get_async_db)
):
    source = await db.get(Session, body.source_session_id)
    if (
        not source
        or source.session_type != SessionType.CLUB
        or source.pool_id != body.pool_id
    ):
        raise HTTPException(422, "Choose a Club session template at this Club's pool")
    settings = get_settings()
    tz = ZoneInfo(source.timezone or "Africa/Lagos")
    current = body.period_start + timedelta(
        days=(body.weekday - body.period_start.weekday()) % 7
    )
    ids = []
    while current <= body.period_end:
        starts = datetime.combine(current, body.starts_at_local, tzinfo=tz)
        ends = starts + timedelta(minutes=body.duration_minutes)
        matching = (
            (
                await db.execute(
                    select(Session)
                    .where(
                        Session.pool_id == body.pool_id,
                        Session.session_type == SessionType.CLUB,
                        Session.starts_at == starts,
                        Session.pod_id == source.pod_id,
                    )
                    .order_by(Session.id)
                )
            )
            .scalars()
            .all()
        )
        if matching:
            ids.extend(row.id for row in matching)
            current += timedelta(days=7)
            continue
        session_id = uuid.uuid5(
            body.club_id, f"quarter:{body.period_start}:{current}:{body.pool_id}"
        )
        ids.append(session_id)
        if await db.get(Session, session_id) is None:
            values = {
                column.name: getattr(source, column.name)
                for column in Session.__table__.columns
                if column.name
                not in {"id", "created_at", "updated_at", "starts_at", "ends_at"}
            }
            # Fresh effective pool/operating rates, existing session margin and
            # manually entered ancillary costs. Never hardcode a Club price.
            pricing = pricing_payload_from_session(source)
            if source.pricing_mode == "cost_plus":
                response = await internal_post(
                    service_url=settings.POOLS_SERVICE_URL,
                    path="/admin/pools/pricing/quote",
                    calling_service="sessions",
                    json={
                        "pool_id": str(body.pool_id),
                        "activity_scope": "club",
                        "starts_at": starts.isoformat(),
                        "ends_at": ends.isoformat(),
                        "timezone": str(tz),
                        "expected_attendees": pricing["pricing_expected_attendees"]
                        or source.capacity,
                        "expected_staff": body.expected_staff,
                        "lanes": body.lanes,
                    },
                )
                if response.status_code >= 400:
                    raise HTTPException(
                        503,
                        "Could not resolve the quarter's pool/operating rates; no draft was published",
                    )
                quoted = response.json()
                if quoted.get("warnings"):
                    raise HTTPException(
                        422,
                        "Configure effective pool rates before generating the quarter: "
                        + "; ".join(quoted["warnings"]),
                    )
                if quoted.get("currency") != "NGN":
                    raise HTTPException(
                        422, "Club session pricing currently requires NGN"
                    )
                pricing["cost_lines"] = quoted["lines"] + [
                    line
                    for line in pricing["cost_lines"]
                    if not line.get("source_rate_id")
                ]
                normalized = normalize_pricing_payload(pricing)
                normalized["pool_fee"] = naira_to_kobo(normalized["pool_fee"])
                values.update(normalized)
            values.update(
                id=session_id,
                starts_at=starts,
                ends_at=ends,
                status=SessionStatus.DRAFT,
                published_at=None,
                template_id=None,
                is_recurring_instance=True,
            )
            await db.execute(
                insert(Session)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Session.id])
            )
        current += timedelta(days=7)
    await db.commit()
    return await query_schedule(ScheduleQuery(session_ids=ids), db)


class ScheduleChange(BaseModel):
    session_ids: list[uuid.UUID] = Field(min_length=1, max_length=52)
    reason: str = Field(min_length=1, max_length=250)


@router.post("/replace")
async def replace_sessions(
    body: ScheduleChange, db: AsyncSession = Depends(get_async_db)
):
    from libs.common.datetime_utils import utc_now

    rows = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(body.session_ids))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(rows) != len(set(body.session_ids)):
        raise HTTPException(422, "An affected session no longer exists")
    for row in rows:
        if row.session_type != SessionType.CLUB or row.starts_at <= utc_now():
            raise HTTPException(409, "Only future Club sessions can be replaced")
        if row.status == SessionStatus.CANCELLED:
            continue
        bookings = (
            await db.execute(
                select(func.count(SessionBooking.id)).where(
                    SessionBooking.session_id == row.id,
                    SessionBooking.status.in_(
                        [SessionBookingStatus.CONFIRMED, SessionBookingStatus.PENDING]
                    ),
                )
            )
        ).scalar_one()
        guests = (
            await db.execute(
                select(func.count(GuestPass.id)).where(
                    GuestPass.session_id == row.id,
                    GuestPass.status.in_(["confirmed", "attended", "pending_payment"]),
                )
            )
        ).scalar_one()
        if bookings or guests:
            raise HTTPException(
                409,
                "This session has bookings; resolve cancellation/refunds through the existing session workflow first",
            )
        row.status = SessionStatus.CANCELLED
        row.notes = f"{row.notes or ''}\n{body.reason}".strip()
    await db.commit()
    return {"replaced": len(rows)}


class PublishSessionInput(BaseModel):
    id: uuid.UUID
    fee_kobo: int
    starts_at: datetime
    pool_id: uuid.UUID


class PublishSessions(BaseModel):
    sessions: list[PublishSessionInput] = Field(min_length=1, max_length=52)


@router.post("/publish")
async def publish_sessions(
    body: PublishSessions, db: AsyncSession = Depends(get_async_db)
):
    from libs.common.datetime_utils import utc_now
    from services.sessions_service.routers.member import (
        trigger_session_published_notifications,
    )

    expected = {item.id: item for item in body.sessions}
    rows = list(
        (
            await db.execute(
                select(Session)
                .where(Session.id.in_(expected))
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    if len(rows) != len(expected):
        raise HTTPException(409, "A selected session no longer exists")
    newly_published = []
    for row in rows:
        item = expected[row.id]
        if (
            row.session_type != SessionType.CLUB
            or row.status not in {SessionStatus.DRAFT, SessionStatus.SCHEDULED}
            or row.starts_at != item.starts_at
            or row.pool_fee != item.fee_kobo
            or row.pool_id != item.pool_id
        ):
            raise HTTPException(
                409, "A selected session changed; review the draft again"
            )
        if row.status == SessionStatus.DRAFT:
            if row.starts_at <= utc_now():
                raise HTTPException(
                    409, "Cannot publish a draft session that has already started"
                )
            row.published_at = utc_now()
            newly_published.append(row)
        row.status = SessionStatus.SCHEDULED
    await db.commit()
    for row in newly_published:
        await trigger_session_published_notifications(
            session_id=row.id, starts_at=row.starts_at, short_notice_message=""
        )
    return [session_snapshot(row) for row in rows]
