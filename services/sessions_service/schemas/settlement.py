"""Internal contracts for paying confirmed walk-ins together."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SessionSettlementQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_auth_id: str = Field(min_length=1)
    booking_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    guest_participant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_subjects(self):
        for ids in (self.booking_ids, self.guest_participant_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate settlement subjects are not allowed")
        return self


class SessionSettlementLine(BaseModel):
    kind: Literal["booking", "guest_walk_in"]
    id: uuid.UUID
    session_id: uuid.UUID
    session_title: str
    session_starts_at: datetime
    name: str
    amount_kobo: int = Field(gt=0)


class SessionSettlementQuoteResponse(BaseModel):
    member_auth_id: str
    session_type: str
    total_kobo: int
    lines: list[SessionSettlementLine]


class SessionSettlementConfirmRequest(SessionSettlementQuoteRequest):
    payment_intent_id: uuid.UUID
    payment_reference: str = Field(min_length=1, max_length=128)
    paid_at: datetime
    total_kobo: int = Field(gt=0)
    lines: list[SessionSettlementLine] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def timezone_required(self):
        if self.paid_at.tzinfo is None:
            raise ValueError("paid_at requires a timezone")
        return self
