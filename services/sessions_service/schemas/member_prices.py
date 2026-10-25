"""Read-only member/session price projections for internal communications."""

from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class MemberPriceCheck(BaseModel):
    context_key: str = Field(min_length=1, max_length=200)
    session_id: UUID
    member_id: UUID


class MemberPricesRequest(BaseModel):
    checks: list[MemberPriceCheck] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [check.context_key for check in self.checks]
        if len(set(keys)) != len(keys):
            raise ValueError("Price context keys must be unique")
        return self


class MemberPriceResponse(BaseModel):
    context_key: str
    fee_amount_kobo: int | None = None
    pricing_source: str | None = None
    price_label: str | None = None


class MemberPricesResponse(BaseModel):
    items: list[MemberPriceResponse]
