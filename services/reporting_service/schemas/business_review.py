"""Schemas for the internal quarter-end business review."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class QuarterComparison(BaseModel):
    current: float | int | None = None
    previous: float | int | None = None
    delta: float | int | None = None
    delta_pct: Optional[float] = None


class BusinessReviewFinance(BaseModel):
    revenue_ngn: int = 0
    expenses_ngn: int = 0
    net_income_ngn: int = 0
    cogs_ngn: int = 0
    gross_margin_ngn: int = 0
    gross_margin_pct: float = 0.0
    profitability_reliable: bool = False
    deferred_revenue_ngn: int = 0
    cash_ngn: int = 0
    by_domain: list[dict[str, Any]] = Field(default_factory=list)
    available: bool = False
    note: Optional[str] = None


class BusinessReviewResponse(BaseModel):
    year: int
    quarter: int
    label: str
    starts_at: datetime
    ends_at: datetime
    snapshot_status: Optional[str] = None
    snapshot_generated_at: Optional[datetime] = None

    executive_scorecard: dict[str, QuarterComparison]
    community: dict[str, Any]
    academy: dict[str, Any]
    club: dict[str, Any]
    finance: BusinessReviewFinance
    locations: list[dict[str, Any]]
    session_mix: dict[str, int]
    data_quality: list[str] = Field(default_factory=list)
    member_distribution_ready: bool = False
    member_distribution_blockers: list[str] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
