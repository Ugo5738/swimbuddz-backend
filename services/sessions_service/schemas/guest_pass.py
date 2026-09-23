import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class GuestPassOffer(BaseModel):
    session_id: uuid.UUID
    title: str
    location_name: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    currency: str = "NGN"
    guest_fee_kobo: Optional[int] = None
    community_dropin_fee_kobo: Optional[int] = None
    allows_guests: bool
    spaces_remaining: Optional[int] = None
    timezone: str = "Africa/Lagos"
    booking_mode: Literal["reservation", "settlement", "closed"] = "closed"
    guest_booking_mode: Literal[
        "disabled", "public", "member_invite", "approval_required"
    ] = "disabled"
    booking_closes_at: Optional[datetime] = None
    reconciliation_closes_at: Optional[datetime] = None
    approval_granted: bool = False
    safety_acknowledgement_version: str = "pool-safety-2026-09"


class GuestPassCreate(BaseModel):
    payment_method: Literal["paystack", "manual_transfer"] = "paystack"
    full_name: str = Field(..., min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(..., min_length=7, max_length=32)
    date_of_birth: Optional[date] = None
    guardian_name: Optional[str] = Field(default=None, max_length=160)
    guardian_phone: Optional[str] = Field(default=None, max_length=32)
    waiver_accepted: bool
    marketing_consent: bool = False
    referral_code: Optional[str] = Field(default=None, max_length=40)

    booking_source: Optional[str] = Field(
        default=None, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    campaign_key: Optional[str] = Field(
        default=None, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    access_token: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def safeguarding(self):
        if not self.waiver_accepted:
            raise ValueError("The safety acknowledgement must be accepted")
        self.full_name = self.full_name.strip()
        self.guardian_name = (self.guardian_name or "").strip() or None
        self.guardian_phone = (self.guardian_phone or "").strip() or None
        if len(self.full_name) < 2:
            raise ValueError("Please enter your full name")
        today = date.today()
        if self.date_of_birth and self.date_of_birth > today:
            raise ValueError("Date of birth cannot be in the future")
        if self.date_of_birth:
            age = (
                today.year
                - self.date_of_birth.year
                - (
                    (today.month, today.day)
                    < (self.date_of_birth.month, self.date_of_birth.day)
                )
            )
            if age < 18 and (not self.guardian_name or not self.guardian_phone):
                raise ValueError(
                    "Guardian name and phone are required for guests under 18"
                )
        return self


class GuestPassPublicResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    price_kobo: int
    additional_charges: list[dict]
    total_kobo: int
    payment_method: str = "paystack"
    payment_reference: str
    status: str
    reservation_expires_at: Optional[datetime] = None
    checkout_url: Optional[str] = None
    receipt_url: Optional[str] = None
    booking_mode: Literal["reservation", "settlement"] = "reservation"
    session_title: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    timezone: str = "Africa/Lagos"
    location_name: Optional[str] = None
    location_address: Optional[str] = None
    attendance_recorded: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GuestPassAdminResponse(GuestPassPublicResponse):
    booking_source: Optional[str] = None
    campaign_key: Optional[str] = None
    confirmation_email_sent_at: Optional[datetime] = None
    full_name: str
    email: EmailStr
    phone: str
    referral_code: Optional[str] = None
    referrer_auth_id: Optional[str] = None
    referral_reward_bubbles: int
    referral_reward_status: str
    marketing_consent: bool
    attended_at: Optional[datetime] = None
    actual_swim_minutes: Optional[int] = None
    assessment_result: Optional[dict[str, Any]] = None
    converted_member_id: Optional[uuid.UUID] = None


class GuestPassConfirm(BaseModel):
    payment_reference: str


class GuestPassAttendanceUpdate(BaseModel):
    actual_swim_minutes: int = Field(..., ge=0, le=1440)
    assessment_result: Optional[dict[str, Any]] = None
    send_assessment_email: bool = True


class GuestBookingGrantCreate(BaseModel):
    email: EmailStr
    expires_in_hours: int = Field(72, ge=1, le=168)


class GuestBookingGrantResponse(BaseModel):
    id: uuid.UUID
    url: str
    expires_at: datetime


class GuestCheckoutRequest(BaseModel):
    payment_method: Literal["paystack", "manual_transfer"] = "paystack"


class GuestLinkEventCreate(BaseModel):
    id: uuid.UUID
    event_type: Literal["view", "share"]
    booking_source: Optional[str] = Field(
        None, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    campaign_key: Optional[str] = Field(
        None, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$"
    )


class GuestFunnelResponse(BaseModel):
    link_views: int
    link_shares: int
    checkout_started: int
    paid: int
    attended: int
    assessed: int
    converted: int


class SessionRosterEntry(BaseModel):
    id: uuid.UUID
    kind: Literal["member", "booking_guest", "guest_pass"]
    full_name: str
    booking_status: str
    attendance_status: Optional[str] = None
    inviter: Optional[str] = None
    booking_mode: Optional[str] = None
    phone: Optional[str] = None
    actual_swim_minutes: Optional[int] = None


class SessionRosterResponse(BaseModel):
    entries: list[SessionRosterEntry]
    attendance_available: bool
