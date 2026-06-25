"""Enrollment and installment schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.academy_service.models import (
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
)

from .cohort import CohortResponse
from .program import ProgramResponse
from .progress import StudentProgressResponse


class EnrollmentBase(BaseModel):
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED
    payment_status: PaymentStatus = PaymentStatus.PENDING


class EnrollmentCreate(BaseModel):
    program_id: UUID
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None


class EnrollmentUpdate(BaseModel):
    status: Optional[EnrollmentStatus] = None
    payment_status: Optional[PaymentStatus] = None
    cohort_id: Optional[UUID] = None  # Allow assigning/changing cohort
    access_suspended: Optional[bool] = None
    missed_installments_count: Optional[int] = None


class EnrollmentInstallmentResponse(BaseModel):
    id: UUID
    installment_number: int
    amount: int  # Kobo (minor NGN unit)
    due_at: datetime
    status: InstallmentStatus
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EnrollmentMarkPaidRequest(BaseModel):
    installment_id: Optional[UUID] = None
    installment_number: Optional[int] = Field(default=None, ge=1)
    clear_installments: bool = False
    payment_reference: Optional[str] = None
    paid_at: Optional[datetime] = None
    # Member-initiated custom amount (kobo). When provided AND larger than the
    # target installment's amount, the payment is applied across multiple
    # installments via apply_member_payment_across_installments. Founder policy
    # May 2026 — see services/academy_service/services/installments.py.
    amount_kobo: Optional[int] = Field(default=None, ge=0)


class AdminDropoutActionRequest(BaseModel):
    """Admin action on an enrollment that is in DROPOUT_PENDING state."""

    action: str  # "approve" → confirm drop, "reverse" → reinstate to ENROLLED
    note: Optional[str] = None  # Optional admin note for the record


class WithdrawEnrollmentRequest(BaseModel):
    """Member-initiated voluntary withdrawal from a cohort."""

    reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Optional reason for withdrawal (for admin visibility).",
    )


class WithdrawEnrollmentResponse(BaseModel):
    """Summary of a withdrawal action — what was refunded and waived."""

    enrollment_id: UUID
    status: str
    window: str  # "before_start", "mid_entry_window", "after_cutoff"
    refund_kobo: int
    refund_percent: float
    paid_kobo: int
    waived_installment_count: int
    payment_references: List[str]  # Payments tagged with refund obligation
    refund_note: str  # Human-readable refund instruction for admin to action

    model_config = ConfigDict(from_attributes=True)


class EnrollmentResponse(EnrollmentBase):
    id: UUID
    program_id: Optional[UUID] = (
        None  # Optional for backward compat if needed, but model has it nullable=True
    )
    cohort_id: Optional[UUID] = None
    member_id: UUID
    preferences: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    total_installments: int = 0
    paid_installments_count: int = 0
    missed_installments_count: int = 0
    access_suspended: bool = False
    uses_installments: bool = False
    # Temporary pause (resumable). NULL = active. While set the student is off
    # the attendance roster and the coach earns nothing for them from this date.
    paused_at: Optional[datetime] = None

    # Include details for UI
    cohort: Optional[CohortResponse] = None
    program: Optional[ProgramResponse] = None
    installments: List[EnrollmentInstallmentResponse] = Field(default_factory=list)

    # Progress records (ORM field is "progress_records", exposed as "progress" for frontend)
    progress: List[StudentProgressResponse] = Field(
        default_factory=list, validation_alias="progress_records"
    )

    # Member info (populated by endpoint, not from ORM)
    member_name: Optional[str] = None
    member_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class EnrollmentPauseResponse(BaseModel):
    """Lightweight result of a pause/resume action (avoids lazy-loading
    cohort/program/progress relationships just to confirm a status change)."""

    id: UUID
    status: str
    paused_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
