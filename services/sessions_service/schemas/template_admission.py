"""Reusable admission defaults. Fees in this API-shaped snapshot are naira."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TemplateAdmissionSettings(BaseModel):
    guest_fee: float | None = Field(None, ge=0, allow_inf_nan=False)
    community_dropin_fee: float | None = Field(None, ge=0, allow_inf_nan=False)
    visiting_club_fee: float | None = Field(None, ge=0, allow_inf_nan=False)
    allows_community_dropins: bool = False
    allows_visiting_club_members: bool = False
    allows_guests: bool = True
    max_guests_per_booking: int = Field(4, ge=0, le=20)
    guest_booking_mode: Literal[
        "disabled", "public", "member_invite", "approval_required"
    ] = "disabled"
    # Relative to each occurrence, never a fixed date copied across a quarter.
    guest_booking_cutoff_hours: int = Field(0, ge=0, le=720)
    guest_reconciliation_days: int = Field(3, ge=0, le=30)
    guest_location_private: bool = False

    @model_validator(mode="after")
    def validate_prices(self) -> "TemplateAdmissionSettings":
        if self.allows_community_dropins and self.community_dropin_fee is None:
            raise ValueError("Set a Community drop-in price when drop-ins are enabled")
        if self.guest_booking_mode != "disabled":
            if not self.allows_guests or self.guest_fee is None:
                raise ValueError("Enable guests and set an explicit guest price")
        return self
