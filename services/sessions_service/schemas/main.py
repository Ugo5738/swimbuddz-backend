import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
    club_id: Optional[uuid.UUID] = None
    club_access_mode: Literal["plan_included", "active_club", "paid_addon"] = (
        "plan_included"
    )
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
    cohort_fee_mode: Literal["included", "paid_extra"] = "included"
    guest_fee: Optional[float] = Field(None, ge=0)
    community_dropin_fee: Optional[float] = None
    allows_community_dropins: bool = False
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
    max_guests_per_booking: int = Field(4, ge=0, le=20)
    guest_booking_mode: Literal[
        "disabled", "public", "member_invite", "approval_required"
    ] = "disabled"
    guest_booking_closes_at: Optional[AwareDatetime] = None
    guest_reconciliation_days: int = Field(3, ge=0, le=30)
    guest_location_private: bool = False

    # Context links
    cohort_id: Optional[uuid.UUID] = None
    event_id: Optional[uuid.UUID] = None
    # Every new CLUB session belongs to one Club. ``pod_id`` optionally
    # narrows the audience to one Pod within that Club.
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
        if self.session_type != SessionType.CLUB and (
            self.club_id or self.club_access_mode != "plan_included"
        ):
            raise ValueError(
                "Only Club sessions may specify a Club or Club access mode"
            )
        if (
            self.cohort_fee_mode != "included"
            and self.session_type != SessionType.COHORT_CLASS
        ):
            raise ValueError(
                "Only a cohort class can be designated as a paid extra class"
            )
        try:
            validate_session_discriminator(
                session_type=self.session_type,
                cohort_id=self.cohort_id,
                event_id=self.event_id,
                club_id=self.club_id,
                pod_id=self.pod_id,
                require_club_id=True,
            )
        except SessionDiscriminatorError as exc:
            # Re-raise as ValueError so Pydantic surfaces a 422
            # validation error with the discriminator message in `detail`.
            raise ValueError(str(exc)) from exc
        if (
            self.session_type == SessionType.CLUB
            and self.allows_community_dropins
            and self.community_dropin_fee is None
        ):
            raise ValueError(
                "community_dropin_fee is required when a Club session allows drop-ins"
            )
        if self.guest_booking_mode != "disabled" and self.guest_fee is None:
            raise ValueError(
                "An explicit guest price is required for guest self-booking (0 for free)"
            )
        starts = (
            self.starts_at
            if self.starts_at.tzinfo
            else self.starts_at.replace(tzinfo=timezone.utc)
        )
        if self.guest_booking_closes_at and self.guest_booking_closes_at > starts:
            raise ValueError(
                "Guest reservation cutoff cannot be after the session starts"
            )
        return self


class SessionUpdate(BaseModel):
    allows_guests: Optional[bool] = None
    max_guests_per_booking: Optional[int] = Field(None, ge=0, le=20)
    guest_booking_mode: Optional[
        Literal["disabled", "public", "member_invite", "approval_required"]
    ] = None
    guest_booking_closes_at: Optional[AwareDatetime] = None
    guest_reconciliation_days: Optional[int] = Field(None, ge=0, le=30)
    guest_location_private: Optional[bool] = None
    club_id: Optional[uuid.UUID] = None
    club_access_mode: Optional[
        Literal["plan_included", "active_club", "paid_addon"]
    ] = None
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
    cohort_fee_mode: Optional[Literal["included", "paid_extra"]] = None
    guest_fee: Optional[float] = Field(None, ge=0)
    community_dropin_fee: Optional[float] = None
    allows_community_dropins: Optional[bool] = None
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

    @model_validator(mode="after")
    def _reject_null_guest_controls(self) -> "SessionUpdate":
        required = {
            "allows_guests",
            "max_guests_per_booking",
            "guest_booking_mode",
            "guest_reconciliation_days",
            "guest_location_private",
        }
        if any(
            getattr(self, name) is None for name in required & self.model_fields_set
        ):
            raise ValueError("Guest admission controls cannot be null")
        return self


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
    access_source: Optional[str] = None
    fee_amount_kobo: Optional[int] = Field(default=None, ge=0)
    price_label: Optional[str] = None


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
            "cohort_fee_mode": getattr(obj, "cohort_fee_mode", None) or "included",
            "guest_fee": guest_fee_kobo / 100.0 if guest_fee_kobo is not None else None,
            "community_dropin_fee": (
                community_dropin_fee_kobo / 100.0
                if community_dropin_fee_kobo is not None
                else None
            ),
            "allows_community_dropins": getattr(obj, "allows_community_dropins", False),
            "ride_share_fee": ride_share_fee_kobo / 100.0,
            **pricing,
            "allows_guests": getattr(obj, "allows_guests", True),
            "guest_booking_mode": getattr(obj, "guest_booking_mode", None)
            or "disabled",
            "guest_booking_closes_at": getattr(obj, "guest_booking_closes_at", None),
            "guest_reconciliation_days": getattr(obj, "guest_reconciliation_days", 3),
            "guest_location_private": getattr(obj, "guest_location_private", False),
            "max_guests_per_booking": getattr(obj, "max_guests_per_booking", 4),
            "cohort_id": obj.cohort_id,
            "event_id": obj.event_id,
            "club_id": getattr(obj, "club_id", None),
            "pod_id": getattr(obj, "pod_id", None),
            "club_access_mode": getattr(obj, "club_access_mode", "plan_included"),
            "week_number": obj.week_number,
            "lesson_title": obj.lesson_title,
            "template_id": obj.template_id,
            "is_recurring_instance": obj.is_recurring_instance,
            "published_at": obj.published_at,
            "created_at": obj.created_at,
            "updated_at": obj.updated_at,
            "access": getattr(obj, "access", None),
        }
