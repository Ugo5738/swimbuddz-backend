"""Schemas for recurring payout configurations and make-up obligations."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from services.payments_service.models import (
    MakeupReason,
    MakeupStatus,
    RecurringPayoutStatus,
)


class RecurringPayoutConfigCreate(BaseModel):
    """Admin payload to create a recurring payout config for a coach+cohort.

    Most cohort details (price, dates, blocks) are pulled from the cohort
    record at the time the config is created and snapshotted onto the
    config so payouts stay stable even if the cohort changes later.
    """

    coach_member_id: uuid.UUID
    cohort_id: uuid.UUID
    band_percentage: Decimal = Field(
        ...,
        ge=Decimal("0.01"),
        le=Decimal("100.00"),
        description="Coach's revenue-share percentage. Must sit within the "
        "cohort's complexity-derived pay band (e.g. 35-42 for "
        "Grade 1 Learn-to-Swim).",
    )
    role: Literal["lead", "assistant"] = Field(
        default="lead",
        description="Coach's role on the cohort. With 2 active coaches the pay "
        "splits 70/30 (lead/assistant), applied at payout time.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Admin rationale, e.g. 'Mid-band: rewards 3yr coaching experience'.",
    )


class RecurringPayoutConfigUpdate(BaseModel):
    """Admin update — only mutable fields. Rates can be adjusted within band;
    cohort snapshot fields are immutable."""

    band_percentage: Optional[Decimal] = Field(
        default=None, ge=Decimal("0.01"), le=Decimal("100.00")
    )
    status: Optional[RecurringPayoutStatus] = None
    notes: Optional[str] = None


class RecurringPayoutConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    coach_member_id: uuid.UUID
    cohort_id: uuid.UUID
    band_percentage: Decimal

    total_blocks: int
    block_length_days: int
    cohort_start_date: datetime
    cohort_end_date: datetime
    cohort_price_amount: int
    currency: str

    # Fixed per-class pay (redesign 2026-06-23). per_class_amount_kobo is the
    # FULL pool rate; the 70/30 split is applied at payout time by role.
    total_classes: Optional[int] = None
    per_class_amount_kobo: Optional[int] = None
    role: str = "lead"

    block_index: int
    next_run_date: datetime
    status: RecurringPayoutStatus

    created_by_member_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class RecurringPayoutConfigListResponse(BaseModel):
    items: List[RecurringPayoutConfigResponse]
    total: int


# ---------------------------------------------------------------------------
# Make-up obligations
# ---------------------------------------------------------------------------


class MakeupObligationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cohort_id: uuid.UUID
    student_member_id: uuid.UUID
    coach_member_id: uuid.UUID
    original_session_id: Optional[uuid.UUID] = None
    scheduled_session_id: Optional[uuid.UUID] = None
    reason: MakeupReason
    status: MakeupStatus
    completed_at: Optional[datetime] = None
    pay_credited_in_payout_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MakeupObligationListResponse(BaseModel):
    items: List[MakeupObligationResponse]
    total: int


class MakeupScheduleRequest(BaseModel):
    """Coach (or admin override) schedules a make-up by linking it to a
    sessions row they've created in the cohort."""

    scheduled_session_id: uuid.UUID
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Computation preview (admin-facing diagnostics)
# ---------------------------------------------------------------------------


class PayoutPreviewLine(BaseModel):
    """Per-student breakdown for a single block payout preview."""

    student_member_id: uuid.UUID
    student_name: Optional[str] = None
    enrolled_at: datetime
    sessions_in_block: int
    sessions_delivered: int  # Counts default-present + LATE + ABSENT(no notice)
    sessions_excused: int  # Excused absences (will become make-ups)
    makeups_completed_in_block: int
    per_session_amount_kobo: int
    student_total_kobo: int


class PayoutPreviewResponse(BaseModel):
    """Dry-run of what the next block payout would produce."""

    config_id: uuid.UUID
    block_index: int
    block_start: datetime
    block_end: datetime
    per_session_amount_kobo: int
    lines: List[PayoutPreviewLine]
    total_kobo: int
    currency: str
