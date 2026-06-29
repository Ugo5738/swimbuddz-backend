"""Core contracts for the Stroke Lab pipeline.

Import-light on purpose (no cv2 / litellm) so these types load anywhere — the
heavy work lives in the components. ``Frame`` is imported from ``coach.frames``,
which is itself import-light (its cv2 use is lazy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from services.ai_service.coach.frames import Frame


class Phase(str, Enum):
    """Where a frame/chunk sits in the freestyle arm cycle. CLIP = whole-clip."""

    ENTRY = "entry"
    CATCH = "catch"
    PULL = "pull"
    RECOVERY = "recovery"
    BREATH = "breath"
    GLIDE = "glide"
    CLIP = "clip"


class Granularity(str, Enum):
    FRAME = "frame"  # judged at a single key instant (e.g. hand entry)
    CHUNK = "chunk"  # judged over a multi-frame arc (e.g. the recovery)


class InputProfile(str, Enum):
    """What the footage can support — drives component availability."""

    SIDE_ON_ABOVE = "side_on_above_water"
    UNDERWATER = "underwater"
    UNKNOWN = "unknown"


class GateTier(str, Enum):
    """The 3-tier gate verdict (see design doc §4)."""

    CLEAN = "clean"  # true side profile → coach fully
    BORDERLINE = "borderline"  # angled but coachable → coach + "film truer side-on"
    REFUSE = "refuse"  # overhead/underwater/head-on/non-freestyle → refund


# A Finding's role in the UX.
SEVERITY_FIX = "fix"
SEVERITY_STRENGTH = "strength"
SEVERITY_INFO = "info"
SEVERITY_UNAVAILABLE = "unavailable"  # "can't see this from this footage"


@dataclass
class FrameRef:
    """A reference to one selected frame (the evidence a Finding points at)."""

    index: int
    timestamp_s: float


@dataclass
class TrackPoint:
    """One sampled frame of the Stage-0 swimmer track."""

    index: int
    timestamp_s: float
    box: Optional[tuple[int, int, int, int]] = None  # swimmer bbox in work-res pixels
    area_frac: float = 0.0  # box area / frame area (the "too small to coach" gate)
    motion: float = 0.0  # upper-box over-water-arm motion signal (drives segmentation)


@dataclass
class Track:
    """Stage-0 output: the swimmer tracked across the whole clip (box only)."""

    points: list[TrackPoint] = field(default_factory=list)
    fps: float = 25.0
    frame_w: int = 0
    frame_h: int = 0
    detector: str = "none"  # "yolo" | "motion"
    fallback_used: bool = False


@dataclass
class Instance:
    """A Stage-1 phase instance — e.g. recovery #3 spanning [start_s, end_s].

    Every visible phase becomes Instances (recovery/entry/glide/breath); recovery
    is split per ``arm`` so the far arm is kept as its own chunks, never discarded.
    """

    phase: Phase
    instance_id: int
    start_s: float
    end_s: float
    peak_s: float
    peak_index: int = 0
    confidence: float = 0.0
    arm: str = "none"  # near | far | none (only recovery is arm-split today)


@dataclass
class Finding:
    """The atomic output of a component — a primitive, not a finished metric."""

    component: str
    observation: str
    severity: str = SEVERITY_INFO
    evidence_frames: list[FrameRef] = field(default_factory=list)
    confidence: float = 0.0
    available: bool = True
    instance_id: Optional[int] = None  # which recovery/breath (Phase 2+)
    area: Optional[str] = None  # closed-enum UX bucket (body_line, recovery_elbow, …)
    extra: dict[str, Any] = field(default_factory=dict)  # component-specific payload


@dataclass
class ComponentResult:
    """One component's run: its findings + telemetry (+ structured meta)."""

    component: str
    findings: list[Finding] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """Model + knob choices. Defaults are the eval-locked picks (design doc §7)."""

    gate_model: Optional[str] = "o4-mini"  # best valid-accept; reasoning
    coach_model: Optional[str] = "gpt-4o"  # 0 honesty violations on the golden set
    segment_model: Optional[str] = (
        "gpt-4o"  # gpt-5-nano labels everything indeterminate
    )
    gate_votes: int = 3
    gate_detail: str = "low"
    coach_detail: str = "auto"
    # Send the clip video (not stills) to the holistic coach. Only video-capable
    # models (Gemini) honour it; ignored on a stills model.
    coach_video: bool = False
    coach_video_max_mb: int = 18
    segment_detail: str = "low"
    segment_batch: int = 12
    max_coached_recoveries: int = (
        1  # don't auto-VLM every recovery — coach a representative
    )
    # Seconds to wait between successive VLM calls within one analysis. Spaces the
    # per-chunk coach + multi-rep aspect calls so several Gemini calls don't burst
    # the free-tier per-minute limit. 0 = no delay (the patient retry is the backstop).
    coach_call_delay_s: float = 0.0


# ── Goal-aware coaching (Stage-2 §12) ─────────────────────────────────────────
# The closed vocabularies shared by the API validator, grade(), and build_goal_block.
DISCIPLINES = ("sprint", "distance", "general")
LEVELS = ("beginner", "intermediate", "advanced")
# Aspect/focus ids == the existing coach/cards.py AREA_LABELS keys, so share-cards
# and the result-page scaffold line up.
ASPECTS = ("body_line", "recovery_elbow", "head_breath", "entry_reach")


@dataclass(frozen=True)
class CoachContext:
    """The swimmer's goal/context from the frontend — steers HOW a finding is
    graded and framed, NEVER what the VLM perceives (design §12.2). The default
    (discipline='general', no extras) reproduces today's discipline-blind behaviour
    exactly, so existing components are unaffected."""

    discipline: str = "general"  # one of DISCIPLINES
    level: Optional[str] = None  # one of LEVELS — tunes drill/tone only
    focus_area: Optional[str] = None  # one of ASPECTS — forces that aspect to rank-1
    goal_text: Optional[str] = (
        None  # free text ≤200 chars; tone only, never an observation
    )


@dataclass
class RunContext:
    """Everything a component needs for one clip. Mutated as stages complete."""

    frames: list[Frame]  # the few key frames for the gate + holistic coach
    strip: list[Frame] = field(
        default_factory=list
    )  # dense strip for Stage-1 segmentation
    profile: InputProfile = InputProfile.UNKNOWN
    config: PipelineConfig = field(default_factory=PipelineConfig)
    stroke_hint: str = "freestyle"
    coaching: CoachContext = field(default_factory=CoachContext)  # goal-aware §12
    # filled in by earlier stages:
    track: Optional[Track] = None  # Stage-0 swimmer track
    gate: Any = None  # coach.coach.GateVerdict, set after the gate component runs
    instances: list[Instance] = field(default_factory=list)  # Stage-1 phase instances
    video_path: Optional[str] = None  # source clip — lets the pose_recovery component
    # decode its own dense frames (the deterministic recovery segmenter)
    pose_recovery: Optional[dict] = None  # Stage-1 pose recovery segmentation result:
    # {count, confidence, detection_rate, near_wrist_conf, refused, peaks_s} — set by
    # pose_recovery (which also splices the recovery instances), read by collate for
    # the confidence/refuse flag. refused=True ⇒ detection gate dropped the recoveries.
    # When set (a dict), components replay their PAID VLM outputs from it instead of
    # calling the API — so reusing a saved run costs $0. None = always call the VLM.
    cache: Optional[dict] = None
    # Accumulated as each analysis component completes (the runner extends it) so a
    # late component (the aggregator) can read EVERY prior finding without a
    # cross-component channel. Empty until the runner starts the analysis loop.
    run_findings: list[Finding] = field(default_factory=list)


@dataclass
class PipelineResult:
    """The whole-clip result: the gate tier + every component's findings."""

    input_profile: InputProfile
    gate_tier: GateTier
    results: list[ComponentResult] = field(default_factory=list)
    total_cost_usd: float = 0.0
    refused: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def findings(self) -> list[Finding]:
        """Flat list of every finding (the collation surface for metrics/UX)."""
        return [f for r in self.results for f in r.findings]
