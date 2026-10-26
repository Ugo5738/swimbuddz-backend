from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TransferAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    access_token: str = Field(min_length=64, max_length=64)


class TransferReceipt(TransferAccess):
    external_reference: str | None = Field(default=None, min_length=3, max_length=128)
    received_date: date
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def normalize_reference(self):
        if self.external_reference is not None:
            self.external_reference = self.external_reference.strip() or None
        return self


class OfflinePaymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    amount_kobo: int = Field(gt=0)
    payment_method: Literal["bank_transfer", "cash", "pos", "other"] = "bank_transfer"
    received_at: datetime
    external_reference: str | None = Field(default=None, max_length=128)
    proof_media_id: UUID | None = None
    note: str = Field(min_length=10, max_length=500)

    @model_validator(mode="after")
    def receipt_required(self):
        if (
            self.payment_method in {"bank_transfer", "pos"}
            and not self.external_reference
            and not self.proof_media_id
        ):
            raise ValueError("Bank transfer/POS requires a transaction reference or receipt")
        return self


class AttachPaymentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proof_media_id: UUID


class TransferSummary(BaseModel):
    reference: str
    purpose: str
    amount_kobo: int
    currency: str
    status: str
    fulfilled: bool
    receipt_attached: bool = False
    reservation_expires_at: datetime | None = None
