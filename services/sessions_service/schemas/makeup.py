"""Schemas for make-up scheduling (Phase 0).

See docs/design/AVAILABILITY_AND_MAKEUP_SCHEDULING_DESIGN.md §8.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.sessions_service.models import (
    MakeupBlockKind,
    MakeupLearnerType,
    MakeupOrigin,
    MakeupStatus,
)


class BookableSlotResponse(BaseModel):
    """A candidate make-up option (UTC) with policy spacing flags.

    ``kind`` is "open" (a dedicated availability gap) or "join_session" (an
    existing session the learner can join — policy §1). For "join_session",
    session_id / session_title / spots_left describe the session.
    """

    start: datetime
    end: datetime
    kind: str = "open"
    session_id: str | None = None
    session_title: str | None = None
    spots_left: int | None = None
    ok: bool = True  # False when spacing warnings exist
    warnings: list[str] = Field(default_factory=list)


class BookableSlotsResponse(BaseModel):
    """Bookable slots for a coach + learner over a date window."""

    coach_id: str
    learner_id: str
    availability_set: bool  # False if the coach hasn't published availability
    slots: list[BookableSlotResponse]


class MakeupBookingCreate(BaseModel):
    """Admin request to confirm a make-up for a learner against a chosen session.

    The session is either a dedicated make-up session (pre-created) or an
    existing one the learner joins (policy §1). ``reason`` is required when
    ``origin`` is ``learner_reschedule`` (policy §4 / 1b).
    """

    learner_member_id: uuid.UUID
    coach_member_id: uuid.UUID
    scheduled_session_id: uuid.UUID
    origin: MakeupOrigin
    reason: str | None = Field(None, max_length=500)
    original_session_id: uuid.UUID | None = None
    block_kind: MakeupBlockKind | None = None
    block_id: uuid.UUID | None = None
    obligation_id: uuid.UUID | None = None
    used_grace: bool = False
    spacing_overridden: bool = False


class MakeupRequestCreate(BaseModel):
    """A learner's self-serve make-up request (admin confirms it later)."""

    coach_member_id: uuid.UUID
    scheduled_session_id: uuid.UUID
    origin: MakeupOrigin = MakeupOrigin.LEARNER_RESCHEDULE
    reason: str | None = Field(None, max_length=500)
    original_session_id: uuid.UUID | None = None


class MakeupOpenSlotCreate(BaseModel):
    """Admin request to create a dedicated make-up session in a coach's *open*
    availability slot and confirm a learner into it in one step (design §4
    Phase 2). The join-an-existing-session path is ``POST /makeups/bookings``;
    this is for booking a brand-new dedicated slot.

    The new session is a ``COHORT_CLASS`` whose cohort comes from ``cohort_id``
    if given, else derived from ``original_session_id``. ``reason`` is required
    when ``origin`` is ``learner_reschedule`` (policy §4 / 1b).
    """

    learner_member_id: uuid.UUID
    coach_member_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    origin: MakeupOrigin
    reason: str | None = Field(None, max_length=500)
    original_session_id: uuid.UUID | None = None
    cohort_id: uuid.UUID | None = None
    pool_id: uuid.UUID | None = None
    title: str | None = Field(None, max_length=200)
    # A dedicated make-up defaults to a single learner; raise it to also let
    # others join the new slot.
    capacity: int = Field(1, ge=1, le=50)
    block_kind: MakeupBlockKind | None = None
    block_id: uuid.UUID | None = None
    obligation_id: uuid.UUID | None = None
    used_grace: bool = False
    spacing_overridden: bool = False

    @model_validator(mode="after")
    def _check(self) -> "MakeupOpenSlotCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at.")
        if self.cohort_id is None and self.original_session_id is None:
            raise ValueError("Provide cohort_id or original_session_id.")
        return self


class MakeupBookingResponse(BaseModel):
    """A make-up booking record."""

    id: uuid.UUID
    learner_member_id: uuid.UUID
    coach_member_id: uuid.UUID
    learner_type: MakeupLearnerType
    origin: MakeupOrigin
    status: MakeupStatus
    block_kind: MakeupBlockKind | None = None
    block_id: uuid.UUID | None = None
    original_session_id: uuid.UUID | None = None
    scheduled_session_id: uuid.UUID | None = None
    used_grace: bool
    notice_hours_at_request: int | None = None
    notes: str | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
