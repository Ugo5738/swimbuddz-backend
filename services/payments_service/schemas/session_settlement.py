"""Admin-issued combined checkout for existing, attended session charges."""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdminSessionSettlementResponse(BaseModel):
    reference: str
    amount: float
    currency: str
    status: str
    authorization_url: str | None
    lines: list[dict[str, Any]]


class AdminSessionSettlementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_auth_id: str = Field(min_length=1)
    booking_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    guest_participant_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    expected_total_kobo: int = Field(gt=0)
    idempotency_key: uuid.UUID
    note: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_subjects(self):
        for ids in (self.booking_ids, self.guest_participant_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate settlement subjects are not allowed")
        return self
