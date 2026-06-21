"""SQLAlchemy models for Stroke Lab — AI swim-video analysis.

Three tables:
  * swim_analysis_jobs    — one row per upload, tracks lifecycle status
  * swim_analysis_results — one row per completed job, stores metrics +
    annotated-video path. Separated from the job so re-running an analysis
    (e.g. after fixing a bug) doesn't require migrating result columns
    onto the job row.
  * swim_frame_labels     — one row per classified frame (phase + arm +
    recovery sub-phase). Normalized out of the coach_result JSON so the
    per-frame classification corpus is queryable for analytics + future
    fine-tuning. No data is discarded — every classified frame is stored.

See docs/design/AI_SWIM_ANALYZER_DESIGN.md and STROKELAB_VLM_COACH_DESIGN.md.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class AnalysisJobStatus(str, enum.Enum):
    """Lifecycle of a Stroke Lab analysis job."""

    PENDING = "pending"  # row created, ARQ task enqueued, not yet picked up
    PROCESSING = "processing"  # worker has picked it up
    COMPLETED = "completed"  # result row exists
    FAILED = "failed"  # error_message is set


class AnalysisJobSource(str, enum.Enum):
    """Who submitted the job: a logged-in member vs an email-gated public guest.

    Public analyzer jobs (analyzer.swimbuddz.com) have no Supabase user, so they
    carry guest_email + guest_token instead of member_auth_id. See
    docs/design/STROKELAB_PUBLIC_ANALYZER_DESIGN.md.
    """

    MEMBER = "member"  # logged-in member; member_auth_id set
    PUBLIC = "public"  # email-gated guest; guest_email + guest_token set


class AnalysisJob(Base):
    """One upload → one analysis job. The row drives state for the API
    polling endpoint and tracks worker timing for queue health."""

    __tablename__ = "swim_analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Owner: the auth_user_id (Supabase user UUID) of the uploader. NULL for
    # public/guest jobs (which are identified by guest_email + guest_token).
    # Indexed because the "list my analyses" endpoint scans by owner.
    member_auth_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Guest identity (public analyzer). NULL for member jobs. guest_email is the
    # quota/credits key; guest_token is a per-job bearer capability for that one
    # result (server-minted on every submit, never client-supplied).
    guest_email: Mapped[Optional[str]] = mapped_column(
        String(320), nullable=True, index=True
    )
    guest_token: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Discriminator: member (has member_auth_id) vs public (has guest_email+token).
    # Namespaced enum-type name per the "enum TYPE names are global" memory note.
    source: Mapped[AnalysisJobSource] = mapped_column(
        Enum(
            AnalysisJobSource,
            name="analysis_job_source_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=AnalysisJobSource.MEMBER,
        server_default=AnalysisJobSource.MEMBER.value,
    )

    # Stroke type the user claims they swam. v0 rejects anything other
    # than "freestyle" at the API layer, but we store the requested value
    # so v1+ can keep history.
    stroke_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Goal-aware coaching context (Stage-2 §12). Steers HOW findings are graded +
    # framed, never what the VLM perceives. Typed columns (like stroke_type), not a
    # DB enum, so the vocabulary can grow without a migration. discipline defaults
    # to "general" so legacy/blank jobs coach exactly as before.
    discipline: Mapped[str] = mapped_column(
        String(16), nullable=False, default="general", server_default="general"
    )
    level: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    focus_area: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    goal_text: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Storage paths. Stored as opaque strings rather than full URLs so we
    # can swap the storage backend without a data migration.
    video_storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    annotated_video_storage_path: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # Lifecycle. Use a namespaced enum-type name per the project memory note
    # "Postgres enum TYPE names are global across services".
    status: Mapped[AnalysisJobStatus] = mapped_column(
        Enum(
            AnalysisJobStatus,
            name="swim_analysis_job_status_enum",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=AnalysisJobStatus.PENDING,
        server_default=AnalysisJobStatus.PENDING.value,
        index=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Visibility: private by default per the design doc. Sharing requires
    # an explicit toggle, which the GET endpoint enforces.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Single-send guard for the public "your analysis is ready" / failure email.
    # The worker sets this inside the SAME transaction as the COMPLETED/FAILED
    # status flip and only sends if it was NULL, so a re-run never re-emails.
    # NULL for member jobs (which don't send these emails).
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    result: Mapped[Optional["AnalysisResult"]] = relationship(
        "AnalysisResult",
        back_populates="job",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<AnalysisJob id={self.id} status={self.status} "
            f"stroke={self.stroke_type}>"
        )


class AnalysisResult(Base):
    """One row per completed analysis. Mirrors the JSON the pipeline
    produces, but in queryable columns so the admin queue page can
    surface aggregate metrics without parsing JSON."""

    __tablename__ = "swim_analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("swim_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Stroke classification — v0 always echoes "freestyle"; v1 will detect.
    detected_stroke: Mapped[str] = mapped_column(String(20), nullable=False)

    # Quality / observability
    pose_detection_rate: Mapped[float] = mapped_column(Float, nullable=False)
    frames_total: Mapped[int] = mapped_column(Integer, nullable=False)
    frames_with_pose: Mapped[int] = mapped_column(Integer, nullable=False)

    # The three v0 metrics
    stroke_rate_spm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    body_roll_proxy_degrees: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    breath_count_left: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    breath_count_right: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    breath_balance_left_ratio: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )

    # LLM-generated 2-3 sentence summary
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Deterministic technique observations (list of dicts: key, severity,
    # title, detail, timestamp_s, drill_key) and tracking-gap intervals
    # (list of {start_s, end_s, duration_s}). Drill copy is resolved from
    # the drill bank at response time, so only drill_key is stored here.
    observations: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    tracking_gaps: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Worker config snapshot — lets us re-run with the same config and
    # explain a result later ("which model version produced this?").
    pipeline_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Full pipeline output for debugging; not exposed in the public
    # response unless explicitly requested.
    raw_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # VLM-coach pipeline result (serialized PipelineResult: gate tier, per-aspect
    # findings, hedged recovery count, costs). Nullable so legacy/metrics-only
    # rows and coach-failures remain valid. This is the "stored run" the result
    # page renders and that further work reuses without re-paying the VLM.
    coach_result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    job: Mapped[AnalysisJob] = relationship(
        "AnalysisJob", back_populates="result", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<AnalysisResult job_id={self.job_id} "
            f"spm={self.stroke_rate_spm} roll={self.body_roll_proxy_degrees}>"
        )


class SwimFrameLabel(Base):
    """One classified frame from the Stage-1 segmenter — the per-frame corpus.

    Normalized out of ``AnalysisResult.coach_result["cache"]["labels"]`` so the
    classification data is queryable (aggregate phase distributions, mine clips by
    sub-phase, export a labelled set for fine-tuning) without parsing JSON. Every
    classified frame is written here — nothing is discarded (the data-loss rule).

    phase / arm / subphase are plain strings (NOT a DB enum) on purpose: the label
    vocabulary will grow (more strokes, more sub-phases) and a data-asset table
    shouldn't need a migration + a global enum-type change every time it does.
    """

    __tablename__ = "swim_frame_labels"
    __table_args__ = (
        UniqueConstraint("job_id", "frame_index", name="uq_frame_label_job_frame"),
        Index("ix_frame_label_phase", "phase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("swim_analysis_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Position of this frame in the dense classification strip.
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_s: Mapped[float] = mapped_column(Float, nullable=False)

    # The VLM classification (one call produced all three fields per frame).
    phase: Mapped[str] = mapped_column(String(24), nullable=False)  # recovery|entry|…
    arm: Mapped[str] = mapped_column(
        String(8), nullable=False, default="none", server_default="none"
    )  # near|far|none
    subphase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", server_default=""
    )  # exit|mid|entry (recovery only) | ""
    conf: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )

    # Provenance for fine-tuning: which classifier (model) produced this label.
    engine_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"<SwimFrameLabel job={self.job_id} f={self.frame_index} "
            f"phase={self.phase} arm={self.arm}>"
        )
