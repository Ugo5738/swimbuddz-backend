"""Constrained pod practices and same-identity rescheduling of promised swims."""

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post, dispatch_notification
from libs.db.session import get_async_db
from services.sessions_service.models import (
    ClubScheduleOperation,
    GuestPass,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionStatus,
    SessionTemplate,
    SessionType,
)
from services.sessions_service.routers.club_schedule import session_snapshot
from services.sessions_service.services.club_generation import (
    club_session_from_template,
)
from services.sessions_service.services.notifications import (
    trigger_session_published_notifications,
)

router = APIRouter(prefix="/sessions/club-operations", tags=["club-operations"])


async def members_operation(action, payload):
    response = await internal_post(
        service_url=get_settings().MEMBERS_SERVICE_URL,
        path=f"/internal/clubs/operations/{action}",
        calling_service="sessions",
        json=payload,
    )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            response.json().get("detail", "Club authority could not be verified"),
        )
    return response.json()


async def pod_authority(user, pod_id):
    return await members_operation(
        "authorize-pod", {"auth_id": user.user_id, "pod_id": str(pod_id)}
    )


async def validate_club_scope(values):
    # Club/Pod ownership is validated by the existing club_scope service.
    # This helper only validates the added access-mode field.
    if values.get(
        "club_access_mode", "plan_included"
    ) != "plan_included" and not values.get("club_id"):
        raise HTTPException(
            422, "Extra practices and paid add-ons require an explicit Club location"
        )
    return values


def future_start(starts):
    if starts.tzinfo is None or not utc_now() < starts <= utc_now() + timedelta(
        days=370
    ):
        raise HTTPException(
            422, "Choose a timezone-aware future time within the next year"
        )


async def no_pod_conflict(db, *, pod_id, starts, ends, exclude_id=None):
    if pod_id is None:
        return
    query = select(Session.id).where(
        Session.pod_id == pod_id,
        Session.status.in_([SessionStatus.DRAFT, SessionStatus.SCHEDULED]),
        Session.starts_at < ends,
        Session.ends_at > starts,
    )
    if exclude_id:
        query = query.where(Session.id != exclude_id)
    if (await db.execute(query)).first():
        raise HTTPException(409, "This pod already has a practice in that time slot")


class ExtraPractice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID
    pod_id: uuid.UUID
    starts_at: datetime
    pool_time_confirmed: bool

    @model_validator(mode="after")
    def confirmed(self):
        if not self.pool_time_confirmed:
            raise ValueError("Confirm the pool has agreed to this practice time")
        return self


@router.get("/pods/{pod_id}")
async def pod_sessions(
    pod_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await pod_authority(user, pod_id)
    rows = (
        await db.execute(
            select(Session)
            .where(
                Session.pod_id == pod_id,
                Session.session_type == SessionType.CLUB,
                Session.starts_at >= utc_now(),
            )
            .order_by(Session.starts_at)
        )
    ).scalars()
    return [session_snapshot(row) for row in rows]


@router.post("/extra-practices")
async def extra_practice(
    body: ExtraPractice,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    authority = await pod_authority(user, body.pod_id)
    payload = {**body.model_dump(mode="json"), "actor": user.user_id}
    await db.execute(
        select(func.pg_advisory_xact_lock(body.operation_id.int % (2**63 - 1)))
    )
    operation = await db.get(ClubScheduleOperation, body.operation_id)
    if operation:
        if operation.kind != "extra_practice" or operation.payload != payload:
            raise HTTPException(409, "Operation ID already used")
        return {
            "session": session_snapshot(
                await db.get(Session, uuid.UUID(operation.after[0]["id"]))
            ),
            "operation_id": str(operation.id),
        }
    future_start(body.starts_at)
    # Serialize a pod's schedule, not every Club at a shared pool.
    await db.execute(select(func.pg_advisory_xact_lock(body.pod_id.int % (2**63 - 1))))
    templates = (
        (
            await db.execute(
                select(SessionTemplate)
                .where(
                    SessionTemplate.club_id == uuid.UUID(authority["club_id"]),
                    SessionTemplate.pool_id == uuid.UUID(authority["pool_id"]),
                    SessionTemplate.session_type == SessionType.CLUB,
                    SessionTemplate.is_active.is_(True),
                )
                .order_by(SessionTemplate.id)
            )
        )
        .scalars()
        .all()
    )
    template = next(
        (
            item
            for item in templates
            if item.pod_id == body.pod_id and item.pricing_settings
        ),
        None,
    )
    template = template or next(
        (item for item in templates if item.pod_id is None and item.pricing_settings),
        None,
    )
    if not template:
        raise HTTPException(
            422,
            "Admin must configure an inherited-price Club template at this pod's pool first",
        )
    if not 15 <= template.duration_minutes <= 180:
        raise HTTPException(422, "Pod practice templates must be 15–180 minutes")
    ends = body.starts_at + timedelta(minutes=template.duration_minutes)
    await no_pod_conflict(db, pod_id=body.pod_id, starts=body.starts_at, ends=ends)
    session = await club_session_from_template(
        template,
        body.starts_at.date(),
        starts=body.starts_at,
        mode="active_club",
        pod_id=body.pod_id,
        capacity=authority["capacity"],
    )
    session.id = uuid.uuid5(body.operation_id, "extra-practice")
    session.status, session.published_at = SessionStatus.SCHEDULED, utc_now()
    db.add(session)
    db.add(
        ClubScheduleOperation(
            id=body.operation_id,
            kind="extra_practice",
            status="applied",
            payload=payload,
            before=[],
            after=[{"id": str(session.id)}],
        )
    )
    await db.commit()
    await trigger_session_published_notifications(
        session_id=session.id, starts_at=session.starts_at
    )
    return {
        "session": session_snapshot(session),
        "operation_id": str(body.operation_id),
    }


class ReschedulePractice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: uuid.UUID
    starts_at: datetime
    reason: str = Field(min_length=5, max_length=250)
    pool_time_confirmed: bool


@router.post("/{session_id}/reschedule")
async def reschedule_practice(
    session_id: uuid.UUID,
    body: ReschedulePractice,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await db.execute(
        select(func.pg_advisory_xact_lock(body.operation_id.int % (2**63 - 1)))
    )
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not session or session.session_type != SessionType.CLUB:
        raise HTTPException(404, "Club session not found")
    if not user.has_role("admin"):
        if not session.pod_id:
            raise HTTPException(403, "Only Admin can move a general Club practice")
        authority = await pod_authority(user, session.pod_id)
        if (
            str(session.club_id) != authority["club_id"]
            or str(session.pool_id) != authority["pool_id"]
        ):
            raise HTTPException(
                403,
                "Only the pod's configured Club and pool can be managed by its lead",
            )
    payload = {
        **body.model_dump(mode="json"),
        "session_id": str(session_id),
        "actor": user.user_id,
    }
    operation = await db.get(ClubScheduleOperation, body.operation_id)
    if operation:
        if operation.kind != "reschedule" or operation.payload != payload:
            raise HTTPException(409, "Operation ID already used")
    else:
        future_start(body.starts_at)
        if session.starts_at <= utc_now() or session.status not in {
            SessionStatus.DRAFT,
            SessionStatus.SCHEDULED,
        }:
            raise HTTPException(
                409, "Only a future draft or scheduled practice can be moved"
            )
        if not body.pool_time_confirmed:
            raise HTTPException(422, "Confirm the pool has agreed to the new time")
        ends = body.starts_at + (session.ends_at - session.starts_at)
        await members_operation(
            "promises",
            {
                "session_id": str(session.id),
                "starts_at": body.starts_at.isoformat(),
                "ends_at": ends.isoformat(),
                "pool_id": str(session.pool_id) if session.pool_id else None,
            },
        )
        if session.pod_id:
            await db.execute(
                select(func.pg_advisory_xact_lock(session.pod_id.int % (2**63 - 1)))
            )
        await no_pod_conflict(
            db,
            pod_id=session.pod_id,
            starts=body.starts_at,
            ends=ends,
            exclude_id=session.id,
        )
        before = session_snapshot(session)
        session.starts_at, session.ends_at = body.starts_at, ends
        # Do not touch price, inclusion identity, bookings, or payment snapshots.
        operation = ClubScheduleOperation(
            id=body.operation_id,
            kind="reschedule",
            status="applied",
            payload=payload,
            before=[before],
            after=[session_snapshot(session)],
            notification_status="pending",
        )
        db.add(operation)
        await db.commit()
    if operation.notification_status != "sent":
        await notify_reschedule(db, operation, session)
    return {
        "session": session_snapshot(session),
        "operation_id": str(operation.id),
        "notification_status": operation.notification_status,
    }


async def notify_reschedule(db, operation, session):
    if datetime.fromisoformat(operation.after[0]["starts_at"]) != session.starts_at:
        operation.notification_status = "superseded"
        await db.commit()
        return
    members = [
        str(value)
        for value in (
            await db.execute(
                select(SessionBooking.member_id).where(
                    SessionBooking.session_id == session.id,
                    SessionBooking.status.in_(
                        [SessionBookingStatus.PENDING, SessionBookingStatus.CONFIRMED]
                    ),
                )
            )
        ).scalars()
    ]
    guests = list(
        (
            await db.execute(
                select(GuestPass.email).where(
                    GuestPass.session_id == session.id,
                    GuestPass.status.in_(["confirmed", "pending_payment"]),
                )
            )
        ).scalars()
    )
    message = f"{session.title} moved from {operation.before[0]['starts_at']} to {operation.after[0]['starts_at']}. {operation.payload['reason']}. Your booking and amount paid are unchanged. Please tell any guests in your booking. Contact SwimBuddz if you cannot attend."
    success = True
    if members:
        success = bool(
            await dispatch_notification(
                type="session_updated",
                category="sessions",
                member_ids=sorted(set(members)),
                title="Club practice rescheduled",
                body=message,
                action_url=f"/sessions/{session.id}",
                calling_service="sessions",
                metadata={"operation_id": str(operation.id)},
            )
        )
    for email in sorted(set(guests)):
        try:
            response = await internal_post(
                service_url=get_settings().COMMUNICATIONS_SERVICE_URL,
                path="/email/send",
                calling_service="sessions",
                json={
                    "to_email": email,
                    "subject": "Club practice rescheduled",
                    "body": message,
                },
            )
            success = success and response.status_code < 400
        except Exception:
            success = False
    operation.notification_status = "sent" if success else "needs_retry"
    await db.commit()


async def authorized_session(db, session_id, user):
    session = await db.get(Session, session_id)
    if not session or session.session_type != SessionType.CLUB:
        raise HTTPException(404, "Club session not found")
    if not user.has_role("admin"):
        if not session.pod_id:
            raise HTTPException(403, "Only Admin manages general Club practices")
        authority = await pod_authority(user, session.pod_id)
        if (
            str(session.club_id) != authority["club_id"]
            or str(session.pool_id) != authority["pool_id"]
        ):
            raise HTTPException(403, "This practice is outside your pod's Club/pool")
    return session


@router.get("/{session_id}/operations")
async def session_operations(
    session_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await authorized_session(db, session_id, user)
    rows = (
        await db.execute(
            select(ClubScheduleOperation)
            .where(
                ClubScheduleOperation.kind == "reschedule",
                ClubScheduleOperation.payload["session_id"].astext == str(session_id),
            )
            .order_by(ClubScheduleOperation.created_at.desc())
            .limit(20)
        )
    ).scalars()
    return [
        {
            "id": row.id,
            "created_at": row.created_at,
            "reason": row.payload["reason"],
            "notification_status": row.notification_status,
            "old_start": row.before[0]["starts_at"],
            "new_start": row.after[0]["starts_at"],
        }
        for row in rows
    ]


@router.post("/{session_id}/operations/{operation_id}/retry-notifications")
async def retry_notifications(
    session_id: uuid.UUID,
    operation_id: uuid.UUID,
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    session = await authorized_session(db, session_id, user)
    await db.execute(select(func.pg_advisory_xact_lock(operation_id.int % (2**63 - 1))))
    operation = await db.get(ClubScheduleOperation, operation_id)
    if (
        not operation
        or operation.kind != "reschedule"
        or operation.payload.get("session_id") != str(session_id)
    ):
        raise HTTPException(404, "Reschedule operation not found")
    if operation.notification_status != "sent":
        await notify_reschedule(db, operation, session)
    return {"notification_status": operation.notification_status}
