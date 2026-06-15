"""Pydantic schemas for Stroke Lab analysis endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Request body ─────────────────────────────────────────────────


class AnalysisJobCreateRequest(BaseModel):
    """Companion JSON for the multipart upload — used when the API caller
    wants to set fields before the file is processed."""

    stroke_type: str = Field("freestyle", description="v0 only accepts freestyle")
    is_public: bool = Field(
        False, description="Make this analysis viewable via a public signed URL"
    )


# ── Lifecycle response (no result yet) ───────────────────────────


class AnalysisJobResponse(BaseModel):
    """Job lifecycle row — what POST returns and what GET returns while
    the worker is still processing."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    member_auth_id: uuid.UUID
    stroke_type: str
    status: str
    error_message: Optional[str] = None
    is_public: bool
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# ── Completed-job response (includes metrics + URLs) ─────────────


class DrillSuggestion(BaseModel):
    """A drill resolved from the drill bank for an observation."""

    key: str
    title: str
    why: str
    how: str
    academy_ref: Optional[str] = None


class Observation(BaseModel):
    """A single deterministic technique flag with a representative moment."""

    key: str
    severity: str  # "good" | "suggestion" | "unavailable"
    title: str
    detail: str
    timestamp_s: Optional[float] = None
    drill: Optional[DrillSuggestion] = None


class TrackingGap(BaseModel):
    start_s: float
    end_s: float
    duration_s: float


class AnalysisResultPayload(BaseModel):
    """The metrics + summary slice exposed to the client. Stripped of
    debug fields like raw_metrics + pipeline_config — those are kept
    server-side."""

    detected_stroke: str
    pose_detection_rate: float
    frames_total: int
    frames_with_pose: int
    stroke_rate_spm: Optional[float] = None
    body_roll_proxy_degrees: Optional[float] = None
    breath_count_left: Optional[int] = None
    breath_count_right: Optional[int] = None
    breath_balance_left_ratio: Optional[float] = None
    summary_text: Optional[str] = None
    observations: list[Observation] = []
    tracking_gaps: list[TrackingGap] = []


class AnalysisJobDetailResponse(AnalysisJobResponse):
    """Single-job detail. Includes the result payload + signed URLs for
    the original + annotated mp4s when available."""

    result: Optional[AnalysisResultPayload] = None
    original_video_url: Optional[str] = None
    annotated_video_url: Optional[str] = None


# ── PUBLIC (guest) analyzer responses ────────────────────────────


class PublicAnalysisJobResponse(BaseModel):
    """What POST /ai/public/analyze returns. No ``member_auth_id`` (guests
    have none); echoes the per-job ``guest_token`` so the FE can store it
    for polling."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: str
    stroke_type: str
    guest_token: str
    estimated_ready_hint: str = (
        "We'll email you when it's ready — usually within a few hours."
    )
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PublicAnalysisJobDetailResponse(BaseModel):
    """GET /ai/public/analyze/{job_id} — guest-facing detail. Mirrors the
    member detail minus ``member_auth_id``/``is_public`` (irrelevant to a
    token-scoped guest read)."""

    model_config = ConfigDict(from_attributes=True)

    job_id: uuid.UUID
    status: str
    stroke_type: str
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[AnalysisResultPayload] = None
    original_video_url: Optional[str] = None
    annotated_video_url: Optional[str] = None
