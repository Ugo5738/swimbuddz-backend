"""Stroke Lab — production swim-video analysis pipeline.

Public surface:

    from services.ai_service.analysis import (
        run_analysis,        # the orchestrator the ARQ task calls
        PipelineConfig,      # tunable knobs (model variant, max side, etc.)
        AnalysisReport,      # what run_analysis returns
        DEFAULT_PIPELINE_CONFIG,
    )

Internals (pose_pipeline, summary) are not part of the stable surface.
"""

from services.ai_service.analysis.pipeline import (
    DEFAULT_PIPELINE_CONFIG,
    AnalysisReport,
    PipelineConfig,
    run_analysis,
)

__all__ = [
    "DEFAULT_PIPELINE_CONFIG",
    "AnalysisReport",
    "PipelineConfig",
    "run_analysis",
]
