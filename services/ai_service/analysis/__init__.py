"""Stroke Lab — production swim-video analysis pipeline.

Public surface (lazily re-exported from .pipeline):

    from services.ai_service.analysis import (
        run_analysis,        # the orchestrator the ARQ task calls
        PipelineConfig,      # tunable knobs (model variant, max side, etc.)
        AnalysisReport,      # what run_analysis returns
        DEFAULT_PIPELINE_CONFIG,
    )

These are re-exported lazily (PEP 562 ``__getattr__``) so importing this
package does NOT pull in the heavy ML stack (cv2 / mediapipe, via
``pose_pipeline``) at module load. Callers that only need the light modules —
``analysis.storage`` / ``analysis.drills``, used by the API service and by
CI's openapi generation, neither of which installs the ML extras — stay clean.
Accessing one of the names below is what triggers the pipeline import (the
worker, which has the extras installed, is the real consumer).

Internals (pose_pipeline, summary) are not part of the stable surface.
"""

__all__ = [
    "DEFAULT_PIPELINE_CONFIG",
    "AnalysisReport",
    "PipelineConfig",
    "run_analysis",
]


def __getattr__(name: str):
    if name in __all__:
        from services.ai_service.analysis import pipeline

        return getattr(pipeline, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
