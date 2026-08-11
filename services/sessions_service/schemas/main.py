import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.sessions_service.models import SessionLocation, SessionStatus, SessionType
from services.sessions_service.models._validators import (
    SessionDiscriminatorError,
    validate_session_discriminator,
)


class SessionCostLine(BaseModel):
    category: str
    description: str
    charge_basis: Literal[
        "per_attendee",
        "per_staff",
        "per_hour",
        "per_lane",
        "flat_session",
    ]
    unit_cost_naira: float = Field(ge=0)
    quantity: float = Field(ge=0)
    source_rate_type: Optional[str] = None
    source_rate_id: Optional[uuid.UUID] = None


class SessionBase(BaseModel):
    title: str
    description: Optional[str] = None
    notes: Optional[str] = None

    session_type: SessionType = SessionType.CLUB
    status: Optional[SessionStatus] = None  # Defaults to DRAFT at creation

    # Location — prefer pool_id (refs pools registry). location / location_name
    # are kept for backwards compatibility with pre-pool-registry sessions.
    pool_id: Optional[uuid.UUID] = None
    location: Optional[SessionLocation] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    # Timing
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Africa/Lagos"

    # Capacity & Fees — API layer uses Naira (float); DB stores kobo (int).
    capacity: int = 20
    pool_fee: float = 0.0  # naira input/output
    guest_fee: Optional[float] = None
    community_dropin_fee: Optional[float] = None
    guest_referral_reward: float = Field(default=1000, ge=0)
    ride_share_fee: float = 0.0  # naira input/output
    pricing_mode: Literal["manual", "cost_plus"] = "manual"
    pricing_expected_attendees: Optional[int] = Field(None, ge=1)
    cost_lines: list[SessionCostLine] = Field(default_factory=list)
    margin_type: Literal["fixed_per_attendee", "percentage"] = "fixed_per_attendee"
    margin_value: float = Field(default=0, ge=0)

    # Guest booking — whether this session accepts non-member guests + the
    # per-booking cap. Defaults mirror the model (on; 4). Lets the booking UI
    # show/hide the guest form and cap guest count per session.
    allows_guests: bool = True
    max_guests_per_booking: int = 4

    # Context links
    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    # CLUB sessions can optionally be tied to a specific pod. NULL means
    # a general Club session (open to any Club member). Cross-service
    # ref → members_service.pods.id (no enforced FK).
    pod_id: Optional[uuid.UUID] = None

    # Cohort-specific
    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class SessionCreate(SessionBase):
    @model_validator(mode="after")
    def _enforce_discriminator(self) -> "SessionCreate":
        """Enforce the session_type → context-FK mapping at API entry.

        See ``services.sessions_service.models._validators`` for the
        rules. A SQLAlchemy ``before_insert`` listener on the Session
        model carries the same enforcement so non-API writers can't
        bypass this.
        """
        try:
            validate_session_discriminator(
                session_type=self.session_type,
                cohort_id=self.cohort_id,
                event_id=self.event_id,
                pod_id=self.pod_id,
            )
        except SessionDiscriminatorError as exc:
            # Re-raise as ValueError so Pydantic surfaces a 422
            # validation error with the discriminator message in `detail`.
            raise ValueError(str(exc)) from exc
        return self


class SessionUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None

    session_type: Optional[SessionType] = None
    status: Optional[SessionStatus] = None

    pool_id: Optional[uuid.UUID] = None
    location: Optional[SessionLocation] = None
    location_name: Optional[str] = None
    location_address: Optional[str] = None

    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    timezone: Optional[str] = None

    capacity: Optional[int] = None
    pool_fee: Optional[float] = None  # naira — router converts to kobo on write
    guest_fee: Optional[float] = None
    community_dropin_fee: Optional[float] = None
    guest_referral_reward: Optional[float] = Field(default=None, ge=0)
    ride_share_fee: Optional[float] = None  # naira — router converts to kobo on write
    pricing_mode: Optional[Literal["manual", "cost_plus"]] = None
    pricing_expected_attendees: Optional[int] = Field(None, ge=1)
    cost_lines: Optional[list[SessionCostLine]] = None
    margin_type: Optional[Literal["fixed_per_attendee", "percentage"]] = None
    margin_value: Optional[float] = Field(None, ge=0)

    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    pod_id: Optional[uuid.UUID] = None

    week_number: Optional[int] = None
    lesson_title: Optional[str] = None


class SessionAccessResponse(BaseModel):
    required_tier: str
    visible: bool
    bookable: bool
    digest_eligible: bool
    prompt_eligible: bool
    sign_in_allowed: bool
    sign_in_eligible: bool
    reason: Optional[str] = None
    message: Optional[str] = None


class MemberSessionAccessResponse(SessionAccessResponse):
    """Authoritative access decision for one member/session pair."""

    member_id: uuid.UUID
    confirmed_booking: bool
    confirmed_booking_id: Optional[uuid.UUID] = None


class SessionResponse(SessionBase):
    id: uuid.UUID
    status: SessionStatus  # Override to make required in response
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    template_id: Optional[uuid.UUID] = None
    is_recurring_instance: bool = False
    access: Optional[SessionAccessResponse] = None
    estimated_total_cost: float = 0
    estimated_cost_per_attendee: float = 0
    margin_amount_per_attendee: float = 0

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _convert_kobo_to_naira(cls, obj):
        """When reading from the ORM, convert kobo fee fields to naira for the API."""
        if isinstance(obj, dict):
            return obj
        # ORM instance: read attributes and convert integer kobo → float naira
        pool_fee_kobo = getattr(obj, "pool_fee", 0) or 0
        ride_share_fee_kobo = getattr(obj, "ride_share_fee", 0) or 0
        guest_fee_kobo = getattr(obj, "guest_fee_kobo", None)
        community_dropin_fee_kobo = getattr(obj, "community_dropin_fee_kobo", None)
        guest_referral_reward_kobo = getattr(obj, "guest_referral_reward_kobo", 100_000)
        from services.sessions_service.services.pricing import pricing_response_fields

        pricing = pricing_response_fields(obj)
        return {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "notes": obj.notes,
            "session_type": obj.session_type,
            "status": obj.status,
            "pool_id": getattr(obj, "pool_id", None),
            "location": obj.location,
            "location_name": obj.location_name,
            "location_address": obj.location_address,
            "starts_at": obj.starts_at,
            "ends_at": obj.ends_at,
            "timezone": obj.timezone,
            "capacity": obj.capacity,
            "pool_fee": pool_fee_kobo / 100.0,
            "guest_fee": guest_fee_kobo / 100.0 if guest_fee_kobo is not None else None,
            "community_dropin_fee": (
                community_dropin_fee_kobo / 100.0
                if community_dropin_fee_kobo is not None
                else None
            ),
            "guest_referral_reward": guest_referral_reward_kobo / 100.0,
            "ride_share_fee": ride_share_fee_kobo / 100.0,
            **pricing,
            "allows_guests": getattr(obj, "allows_guests", True),
            "max_guests_per_booking": getattr(obj, "max_guests_per_booking", 4),
            "cohort_id": obj.cohort_id,
            "event_id": obj.event_id,
            "pod_id": getattr(obj, "pod_id", None),
            "week_number": obj.week_number,
            "lesson_title": obj.lesson_title,
            "template_id": obj.template_id,
            "is_recurring_instance": obj.is_recurring_instance,
            "published_at": obj.published_at,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "access": getattr(obj, "access", None),
        }
