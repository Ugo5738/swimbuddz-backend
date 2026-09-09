"""Service-owned Club schedule and costing snapshots, without cross-service SQL."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.sessions_service.models import (
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionType,
    SessionTemplate,
    ClubScheduleOperation,
)
from services.sessions_service.schemas.templates import ClubTemplatePricing
from services.sessions_service.services.club_generation import (
    club_session_from_template,
    club_instance_id,
    recurrence_dates,
)

router = APIRouter(
    prefix="/internal/sessions/club-schedule",
    tags=["internal-club-schedule"],
    dependencies=[Depends(require_service_role)],
)


class ScheduleQuery(BaseModel):
    club_id: uuid.UUID | None = None
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
        "club_id": str(session.club_id) if session.club_id else None,
        "club_access_mode": session.club_access_mode,
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
    if body.club_id:
        query = query.where(Session.club_id == body.club_id)
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
    pool_id: uuid.UUID
    template_id: uuid.UUID | None = None
    title: str = "Club practice"
    period_start: date
    period_end: date
    weekday: int = Field(ge=0, le=6)
    starts_at_local: time
    duration_minutes: int = Field(ge=15, le=480)
    capacity: int = Field(default=20, ge=1, le=500)
    pricing_settings: ClubTemplatePricing | None = None
    excluded_dates: list[date] = Field(default_factory=list, max_length=52)

    @model_validator(mode="after")
    def valid_range(self):
        if not 0 <= (self.period_end - self.period_start).days <= 100:
            raise ValueError("Generate at most one quarter at a time")
        return self


@router.post("/generate")
async def generate_quarter(
    body: GenerateQuarter, db: AsyncSession = Depends(get_async_db)
):
    template_id = body.template_id or uuid.uuid5(body.club_id, "primary-club-template")
    # Serialize generation for this Club/template. Repeated requests return the
    # same Sessions and never overwrite a published Session or its price.
    await db.execute(select(func.pg_advisory_xact_lock(template_id.int % (2**63 - 1))))
    template = await db.get(SessionTemplate, template_id)
    if template is None:
        if body.template_id or body.pricing_settings is None:
            raise HTTPException(
                422,
                "Choose a Club template or supply expected attendance and margin for the first recommendation",
            )
        template = SessionTemplate(
            id=template_id,
            title=body.title,
            session_type=SessionType.CLUB,
            club_id=body.club_id,
            pool_id=body.pool_id,
            club_access_mode="plan_included",
            day_of_week=body.weekday,
            start_time=body.starts_at_local,
            duration_minutes=body.duration_minutes,
            capacity=body.capacity,
            pricing_settings=body.pricing_settings.model_dump(mode="json"),
            auto_generate=False,
            is_active=True,
        )
        db.add(template)
        await db.flush()
    if (
        not template.is_active
        or template.session_type != SessionType.CLUB
        or template.club_id != body.club_id
        or template.pool_id != body.pool_id
        or template.club_access_mode != "plan_included"
        or template.pod_id is not None
    ):
        raise HTTPException(
            422, "Choose this Club's active primary, home-pool inclusion template"
        )
    if body.pricing_settings:
        template.pricing_settings = {
            **template.pricing_settings,
            **body.pricing_settings.model_dump(mode="json", exclude_unset=True),
        }
    ids = []
    for day in recurrence_dates(
        template, body.period_start, body.period_end, set(body.excluded_dates)
    ):
        starts = datetime.combine(
            day, template.start_time, tzinfo=ZoneInfo(get_settings().TIMEZONE)
        )
        session_id = club_instance_id(template.id, starts, template.pod_id)
        row = await db.get(Session, session_id)
        if row is None:
            row = await club_session_from_template(template, day)
            db.add(row)
        ids.append(row.id)
    await db.commit()
    return await query_schedule(ScheduleQuery(session_ids=ids), db) if ids else []


class ScheduleChange(BaseModel):
    operation_id: uuid.UUID
    validate_only: bool = False
    session_ids: list[uuid.UUID] = Field(min_length=1, max_length=52)
    reason: str = Field(min_length=1, max_length=250)


@router.post("/replace")
async def replace_sessions(
    body: ScheduleChange, db: AsyncSession = Depends(get_async_db)
):
    from libs.common.datetime_utils import utc_now

    await db.execute(
        select(func.pg_advisory_xact_lock(body.operation_id.int % (2**63 - 1)))
    )
    operation = await db.get(ClubScheduleOperation, body.operation_id)
    payload = body.model_dump(mode="json", exclude={"validate_only"})
    if operation:
        if operation.status == "reverted":
            raise HTTPException(
                409, "This operation was compensated; start a new configuration attempt"
            )
        if operation.kind != "replace" or operation.payload != payload:
            raise HTTPException(409, "Operation ID already used for different changes")
        return {"replaced": len(operation.after), "operation_id": str(operation.id)}

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
    before = [
        {"id": str(row.id), "status": row.status.value, "notes": row.notes}
        for row in rows
    ]
    for row in rows:
        if row.session_type != SessionType.CLUB or row.starts_at <= utc_now():
            raise HTTPException(409, "Only future Club sessions can be replaced")
        from services.sessions_service.routers.club_operations import members_operation

        promises = await members_operation("promises", {"session_id": str(row.id)})
        if promises["published_promise"]:
            raise HTTPException(
                409,
                "This swim is promised in a published quarter; reschedule it instead",
            )
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
    if body.validate_only:
        return {"valid": True}
    for row in rows:
        row.status = SessionStatus.CANCELLED
        row.notes = f"{row.notes or ''}\n{body.reason}".strip()
    db.add(
        ClubScheduleOperation(
            id=body.operation_id,
            kind="replace",
            status="applied",
            payload=payload,
            before=before,
            after=[
                {"id": str(row.id), "status": row.status.value, "notes": row.notes}
                for row in rows
            ],
        )
    )
    await db.commit()
    return {"replaced": len(rows)}


@router.post("/operations/{operation_id}/undo")
async def undo_replacement(
    operation_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    await db.execute(select(func.pg_advisory_xact_lock(operation_id.int % (2**63 - 1))))
    operation = await db.get(ClubScheduleOperation, operation_id)
    if operation is None:
        # Tombstone fences a delayed replacement request after a timeout.
        db.add(
            ClubScheduleOperation(
                id=operation_id,
                kind="replace",
                status="reverted",
                payload={},
                before=[],
                after=[],
            )
        )
    elif operation.kind != "replace":
        raise HTTPException(409, "Only an unsold replacement can be compensated")
    elif operation.status != "reverted":
        for before, after in zip(operation.before, operation.after, strict=True):
            row = (
                await db.execute(
                    select(Session)
                    .where(Session.id == uuid.UUID(before["id"]))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                not row
                or row.status.value != after["status"]
                or row.notes != after["notes"]
            ):
                raise HTTPException(
                    409,
                    "Session changed since replacement; Admin reconciliation required",
                )
            row.status, row.notes = SessionStatus(before["status"]), before["notes"]
        operation.status = "reverted"
    await db.commit()
    return {"status": "reverted"}


class PublishSessionInput(BaseModel):
    id: uuid.UUID
    fee_kobo: int
    starts_at: datetime
    ends_at: datetime
    pool_id: uuid.UUID


class PublishSessions(BaseModel):
    club_id: uuid.UUID
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
            or row.ends_at != item.ends_at
            or row.club_id not in (None, body.club_id)
            or row.club_access_mode != "plan_included"
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
        row.club_id = body.club_id
        row.status = SessionStatus.SCHEDULED
    await db.commit()
    for row in newly_published:
        await trigger_session_published_notifications(
            session_id=row.id, starts_at=row.starts_at, short_notice_message=""
        )
    return [session_snapshot(row) for row in rows]
