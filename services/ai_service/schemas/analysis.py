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


class InspectRequest(BaseModel):
    """Per-instance drilldown request (§12.5) — coach one stored instance of an
    aspect on demand. Gated (409) until segmentation count accuracy clears the bar."""

    aspect: str = Field(description="aspect id: body_line | recovery_elbow | …")
    instance_id: int = Field(ge=0, description="which instance of that aspect/phase")


class PublicDirectUploadRequest(BaseModel):
    """Start a direct browser-to-S3 public analyzer upload."""

    guest_email: str
    filename: str = "clip.mp4"
    content_type: str = "video/mp4"
    size_bytes: int = Field(gt=0)
    stroke_type: str = "freestyle"
    discipline: str = "general"
    level: Optional[str] = None
    focus_area: Optional[str] = None
    goal_text: Optional[str] = None


class PublicDirectUploadResponse(BaseModel):
    """Private presigned PUT target for a public analyzer upload."""

    job_id: uuid.UUID
    guest_token: str
    upload_url: str
    method: str = "PUT"
    headers: dict[str, str] = {}
    expires_in: int


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

    # detected_stroke echoes the requested stroke; the pose-derived observability
    # fields are NULL on coach-primary runs (the pose pass is retired).
    detected_stroke: Optional[str] = None
    pose_detection_rate: Optional[float] = None
    frames_total: Optional[int] = None
    frames_with_pose: Optional[int] = None
    # The pivot BANS these numbers (over-counted spm, unreliable roll degrees,
    # false-firing breath counts). build_result_payload always leaves them None so
    # the UI can never re-surface a number we can't defend. Kept nullable only for
    # back-compat with older clients that read the keys.
    stroke_rate_spm: Optional[float] = None
    body_roll_proxy_degrees: Optional[float] = None
    breath_count_left: Optional[int] = None
    breath_count_right: Optional[int] = None
    breath_balance_left_ratio: Optional[float] = None
    summary_text: Optional[str] = None
    observations: list[Observation] = []
    tracking_gaps: list[TrackingGap] = []
    # Sanitized per-phase instances (whitelisted projection of the stored segmenter
    # output — NEVER the raw VLM cache). Populated only when the per-instance
    # drilldown is unlocked (§12.5); None while locked. Powers the recovery browser.
    instances: Optional[list[dict]] = None
    # VLM-coach result (the PipelineResult slice: gate tier + per-aspect findings
    # + hedged recovery count). None for legacy/metrics-only rows or coach failures.
    # The internal VLM cache is NOT exposed here — only the derived result.
    coach_result: Optional[dict] = None
    # Signed evidence-frame URLs, keyed "<component>:<index>" (matches a finding's
    # component + evidence_frames[].index). Signed at response time; None if absent.
    coach_evidence_urls: Optional[dict[str, str]] = None
    # Signed shareable-card URLs, same "<component>:<index>" keying (one per FIX
    # finding). Signed at response time; None if absent.
    coach_share_urls: Optional[dict[str, str]] = None
    # Per-instance on-demand coach jobs, keyed "<aspect>:<instance_id>". This gives
    # the frontend status/backoff visibility while a stroke read is queued/retrying.
    inspect_statuses: Optional[dict[str, dict]] = None


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
    credits_remaining: int = 0
    estimated_ready_hint: str = "We'll email you a link as soon as it's ready."
    queue_depth: Optional[int] = None
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
    discipline: str = "general"  # the goal the analysis was coached for (§12)
    drilldown_unlocked: bool = (
        False  # per-stroke inspect available (config gate, §12.5)
    )
    timeline_unlocked: bool = False  # the video-led timeline view (v2) is available
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[AnalysisResultPayload] = None
    original_video_url: Optional[str] = None
    annotated_video_url: Optional[str] = None
    queue_depth: Optional[int] = None


class GumroadRedeemRequest(BaseModel):
    """Body for POST /ai/public/credits/redeem — the different-email fallback."""

    email: str
    license_key: str
    product_permalink: str


class GumroadRedeemResponse(BaseModel):
    granted: int
    remaining_credits: int


class PublicCreditsResponse(BaseModel):
    """GET /ai/public/credits — coarse, non-enumerable balance. ``free_used`` is
    intentionally NOT exposed (it is the 'has this email been used' leak)."""

    email: str
    can_submit_free: bool
    remaining_credits: int
