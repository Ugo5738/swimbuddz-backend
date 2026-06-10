"""Public orchestrator for the Stroke Lab analysis pipeline.

This is what the ARQ task and the CLI call. It glues together pose
detection (pose_pipeline) and the LLM summary (summary), and returns a
single typed dataclass.

Storage I/O (downloading the upload, uploading the annotated video) is
the caller's responsibility — this module operates on local file paths.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from services.ai_service.analysis.pose_pipeline import analyse_video
from services.ai_service.analysis.summary import generate_summary


@dataclass(frozen=True)
class PipelineConfig:
    """Tunable knobs. The defaults are the kill-gate winner config.

    See the Week 1 validation results in the conversation transcript
    of 2026-05-13 for why these values were chosen.
    """

    pose_model_variant: str = "lite"
    max_inference_side: int = 1280
    use_yolo: bool = True
    yolo_conf_threshold: float = 0.25
    # Process every N-th frame. 1 = no skip (highest quality). 2 = halves
    # CPU time and keeps metric quality intact on 24fps phone footage.
    frame_stride: int = 2
    # Padding around the YOLO box before cropping for pose, as a fraction
    # of the box's own width/height.
    box_pad_ratio: float = 0.18
    # If True, ask the LLM for a short qualitative summary. Set False in
    # tests / when the API key is absent.
    enable_summary: bool = True


DEFAULT_PIPELINE_CONFIG = PipelineConfig()


@dataclass
class AnalysisReport:
    """What ``run_analysis`` returns.

    Everything here is JSON-serialisable so it round-trips into the
    AnalysisResult DB row cleanly.
    """

    annotated_video_path: Path

    # Quality / observability
    source_resolution: str
    duration_seconds: float
    fps: float
    frames_total: int
    frames_processed: int
    frames_with_pose: int
    pose_detection_rate: float
    yolo_detection_rate: float
    processing_seconds: float
    realtime_ratio: Optional[float]

    # v0 metrics
    detected_stroke: str
    stroke_rate_spm: Optional[float] = None
    body_roll_proxy_degrees: Optional[float] = None
    breath_count_left: Optional[int] = None
    breath_count_right: Optional[int] = None
    breath_balance_left_ratio: Optional[float] = None

    # LLM
    summary_text: Optional[str] = None

    # Deterministic technique observations + tracking-gap intervals
    observations: list = field(default_factory=list)
    tracking_gaps: list = field(default_factory=list)

    # Debug
    config_snapshot: dict = field(default_factory=dict)
    raw_metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["annotated_video_path"] = str(self.annotated_video_path)
        return d


async def run_analysis(
    video_path: Path,
    annotated_out_path: Path,
    *,
    stroke_type: str = "freestyle",
    config: PipelineConfig = DEFAULT_PIPELINE_CONFIG,
) -> AnalysisReport:
    """End-to-end analysis of one swim video.

    Runs the (synchronous, CPU-bound) pose pipeline in a thread so it
    doesn't block the ARQ event loop, then awaits the LLM summary.
    """
    metrics = await asyncio.to_thread(
        analyse_video,
        video_path,
        annotated_out_path,
        pose_model_variant=config.pose_model_variant,
        max_inference_side=config.max_inference_side,
        use_yolo=config.use_yolo,
        yolo_conf_threshold=config.yolo_conf_threshold,
        frame_stride=config.frame_stride,
        box_pad_ratio=config.box_pad_ratio,
    )

    summary: Optional[str] = None
    if config.enable_summary:
        summary = await generate_summary(metrics, stroke_type=stroke_type)

    return AnalysisReport(
        annotated_video_path=annotated_out_path,
        source_resolution=metrics.get("source_resolution", ""),
        duration_seconds=metrics.get("duration_seconds", 0.0),
        fps=metrics.get("fps", 0.0),
        frames_total=metrics.get("frames_total", 0),
        frames_processed=metrics.get("frames_processed", 0),
        frames_with_pose=metrics.get("frames_with_pose", 0),
        pose_detection_rate=metrics.get("pose_detection_rate", 0.0),
        yolo_detection_rate=metrics.get("yolo_detection_rate", 0.0),
        processing_seconds=metrics.get("processing_seconds", 0.0),
        realtime_ratio=metrics.get("realtime_ratio"),
        # v0 only detects freestyle — echo what the user uploaded
        detected_stroke=stroke_type,
        stroke_rate_spm=metrics.get("stroke_rate_spm"),
        body_roll_proxy_degrees=metrics.get("body_roll_proxy_degrees"),
        breath_count_left=metrics.get("breath_count_left"),
        breath_count_right=metrics.get("breath_count_right"),
        breath_balance_left_ratio=metrics.get("breath_balance_left_ratio"),
        summary_text=summary,
        observations=metrics.get("observations", []),
        tracking_gaps=metrics.get("tracking_gaps", []),
        config_snapshot=dataclasses.asdict(config),
        raw_metrics=metrics,
    )
