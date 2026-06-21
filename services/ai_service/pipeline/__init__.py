"""Stroke Lab analysis pipeline — a staged, plug-and-play component framework.

The system is a set of **components** behind one interface, run in stages:

    Stage 0  INGEST & TRACK   (shared; full-video swimmer track)        [Phase 2]
    Stage 1  SEGMENT          (shared; recovery/breath instances)       [Phase 2]
    Stage 2  ANALYZE          (components — toggleable: gate, coach, …)  [Phase 1]
    Stage 3  COLLATE          (derive UX sections + metrics)            [Phase 1]

Design principles (see docs/design/STROKELAB_VLM_COACH_DESIGN.md):
  * Components emit PRIMITIVES (``Finding``), never finished metrics — metrics
    are derived downstream, so a new metric never re-runs analysis.
  * Every ``Finding`` carries confidence + evidence + an ``available`` flag, so a
    plugged-in component cannot silently bluff.
  * A component's ``available(profile)`` is a function of the INPUT footage, so
    underwater-only components (catch/pull/kick) ship dormant and light up when
    underwater footage exists — built-in, not bolted-on.

Phase 1 (this package) builds the framework and wraps the already-validated gate
and holistic coach as the first two components. It does NOT rewrite them.
"""

from __future__ import annotations

# Re-export the stable surface. Kept import-light (no cv2 / litellm at import).
from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.registry import Registry
from services.ai_service.pipeline.types import (
    ComponentResult,
    Finding,
    FrameRef,
    GateTier,
    Granularity,
    InputProfile,
    Phase,
    PipelineConfig,
    PipelineResult,
    RunContext,
)

__all__ = [
    "Component",
    "Registry",
    "ComponentResult",
    "Finding",
    "FrameRef",
    "GateTier",
    "Granularity",
    "InputProfile",
    "Phase",
    "PipelineConfig",
    "PipelineResult",
    "RunContext",
]
