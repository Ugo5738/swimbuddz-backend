"""Explicit participant/ticket inputs; clients never supply ticket amounts."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class ExperienceEventLinkInput(BaseModel):
    event_id: uuid.UUID
    club_impact: Literal["separate", "parallel", "replaces"] = "separate"
    replaced_session_ids: list[uuid.UUID] = Field(default_factory=list, max_length=52)

    @model_validator(mode="after")
    def explicit_replacement(self):
        if bool(self.replaced_session_ids) != (self.club_impact == "replaces"):
            raise ValueError(
                "Only replaces Club requires explicit affected session IDs"
            )
        return self


class ExperienceEventsUpdate(BaseModel):
    events: list[ExperienceEventLinkInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_events(self):
        if len({event.event_id for event in self.events}) != len(self.events):
            raise ValueError("An Event may only be linked once")
        return self


class ExperienceParticipantInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    phone: str = Field(min_length=7, max_length=40)
    emergency_contact_name: str = Field(min_length=2, max_length=160)
    emergency_contact_phone: str = Field(min_length=7, max_length=40)
    waiver_accepted: Literal[True]


class ExperienceOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A random UUID generated once by the browser and retained on retry.
    idempotency_key: uuid.UUID
    access_token: str = Field(min_length=32, max_length=128)
    include_member: bool = True
    participant: ExperienceParticipantInput
    guests: list[ExperienceParticipantInput] = Field(
        default_factory=list, max_length=20
    )


class ExperienceOrderResponse(BaseModel):
    id: uuid.UUID
    offering_id: uuid.UUID
    status: str
    currency: str
    amount_kobo: int
    membership_fee_kobo: int
    expires_at: datetime
    payment_reference: str
    participant_count: int
    tickets: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class ExperienceOrderAccess(BaseModel):
    access_token: str = Field(min_length=32, max_length=128)


class ExperienceParticipantUpdate(ExperienceOrderAccess):
    participant: ExperienceParticipantInput


class ExperienceOrderConfirm(BaseModel):
    payment_reference: str = Field(min_length=1, max_length=128)
    amount_kobo: int = Field(ge=0)


class ExperienceAttendanceInput(BaseModel):
    event_id: uuid.UUID
