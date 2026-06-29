"""THE pipeline control surface — the stage flow + per-component on/off.

This is the one file to read to understand the flow and flip pieces on/off.

    FLOW (per clip):
      gate            ALWAYS   3-tier view/usability decision; REFUSE short-circuits
                               the whole pipeline before any coaching cost.
      phase_segment   Stage 1  STROKELAB_COACH_SEGMENT   classify EVERY frame (one VLM
                               call: phase + arm + recovery sub-phase, all stored) and
                               group into ctx.instances (every visible phase, both arms).
                               No counting, no coaching here.
      pose_recovery   Stage 1  STROKELAB_COACH_POSE_RECOVERY  DETERMINISTIC near-arm
                               recovery SEGMENTER (yolov8-pose wrist trajectory). REPLACES
                               the near-arm recovery instances phase_segment produced with
                               pose-derived ones (one per peak); DROPS them on a low-
                               detection REFUSE so the count + drilldown vanish together.
                               Keeps the VLM far-arm + other phases. Counting stays in
                               collate — this only SEGMENTS. Worker CPU.
      recovery_coach  Stage 2  STROKELAB_COACH_RECOVERY  coach a representative near-arm
                               recovery (coaches the pose instances when pose_recovery is on).
      body_line       Stage 2  STROKELAB_COACH_BODY_LINE  head/hip/leg sink from a glide
                               frame (goal-aware aspect; OFF by default until its eval).
      entry_reach     Stage 2  STROKELAB_COACH_ENTRY  hand entry/reach (crossover banned;
                               sprint dead-spot hedged; OFF by default until its eval).
      head_breathing  Stage 2  STROKELAB_COACH_HEAD  head carriage + breath side (never a
                               rhythm number; OFF by default until its eval).
      holistic_coach  Stage 2  STROKELAB_COACH_HOLISTIC  whole-clip coaching (independent).
      collate         Stage 3  STROKELAB_COACH_COLLATE   derive counts/metrics from the
                               (pose-segmented when on) ctx.instances → the hedged
                               "~N recoveries" summary; suppressed on a pose REFUSE.
      catch / pull /  dormant  STROKELAB_COACH_UNDERWATER  underwater-only; on above-water
      flutter_kick             footage each emits an honest "can't see this" card. Off by
                               default until an underwater profile + analyzer exist.

    Also gated, in the worker (not components):
      STROKELAB_ENABLE_COACH        the whole coach on/off
      STROKELAB_COACH_SHARE_CARDS   render shareable per-finding cards
      STROKELAB_COACH_*_MODEL       which model each layer uses

Toggle via config / the env files (libs/common/config.py + .env.dev/.env.prod), or
at call time with ``Registry.set_enabled(name, on)`` / ``run.py --disable <name>``.
"""

from __future__ import annotations

from services.ai_service.pipeline.components.aggregator import AggregatorComponent
from services.ai_service.pipeline.components.body_line import BodyLineComponent
from services.ai_service.pipeline.components.chunk_coach import ChunkCoachComponent
from services.ai_service.pipeline.components.collate import CollateComponent
from services.ai_service.pipeline.components.entry_reach import EntryReachComponent
from services.ai_service.pipeline.components.gate import GateComponent
from services.ai_service.pipeline.components.head_breathing import (
    HeadBreathingComponent,
)
from services.ai_service.pipeline.components.holistic_coach import (
    HolisticCoachComponent,
)
from services.ai_service.pipeline.components.pose_recovery import PoseRecoveryComponent
from services.ai_service.pipeline.components.recovery_coach import (
    RecoveryCoachComponent,
)
from services.ai_service.pipeline.components.segment import PhaseSegmentComponent
from services.ai_service.pipeline.components.underwater import (
    CatchComponent,
    FlutterKickComponent,
    PullComponent,
)
from services.ai_service.pipeline.registry import Registry


# aspect id (AREA_LABELS / grade key) → the component class that coaches it. Used
# by the on-demand inspect path to coach a single instance of a chosen aspect.
_ASPECT_COMPONENTS = {
    "recovery_elbow": RecoveryCoachComponent,
    "body_line": BodyLineComponent,
    "entry_reach": EntryReachComponent,
    "head_breath": HeadBreathingComponent,
    # The full multi-aspect read for ONE stroke — the SAME component the free flow
    # runs on the pre-coached chunks, so on-demand and the full flow are identical.
    "chunk": ChunkCoachComponent,
}


def aspect_component(aspect: str):
    """The AspectCoachComponent class for an aspect id, or None if unknown."""
    return _ASPECT_COMPONENTS.get(aspect)


def build_default_registry() -> Registry:
    """Register the components in flow order, honouring the config toggles."""
    from libs.common.config import get_settings

    s = get_settings()
    reg = Registry()
    reg.register(
        GateComponent()
    )  # IS_GATE — always; runs first, sets the 3-tier branch
    reg.register(PhaseSegmentComponent(), enabled=s.STROKELAB_COACH_SEGMENT)
    # pose_recovery runs AFTER phase_segment (it splices the VLM instances) and BEFORE
    # the per-instance coaches (so they coach the pose-derived recovery instances).
    reg.register(PoseRecoveryComponent(), enabled=s.STROKELAB_COACH_POSE_RECOVERY)
    reg.register(RecoveryCoachComponent(), enabled=s.STROKELAB_COACH_RECOVERY)
    # Chunk-centric coach: coaches each free recovery chunk on every visible aspect
    # (one video call/chunk). Replaces holistic + the standalone aspect coaches when on.
    reg.register(ChunkCoachComponent(), enabled=s.STROKELAB_COACH_CHUNK)
    reg.register(BodyLineComponent(), enabled=s.STROKELAB_COACH_BODY_LINE)
    reg.register(EntryReachComponent(), enabled=s.STROKELAB_COACH_ENTRY)
    reg.register(HeadBreathingComponent(), enabled=s.STROKELAB_COACH_HEAD)
    reg.register(HolisticCoachComponent(), enabled=s.STROKELAB_COACH_HOLISTIC)
    reg.register(CollateComponent(), enabled=s.STROKELAB_COACH_COLLATE)
    # Aggregator runs LAST (after the chunk coach + collate) so ctx.run_findings holds
    # every per-chunk finding for it to collate into the summary + top fixes.
    reg.register(AggregatorComponent(), enabled=s.STROKELAB_COACH_AGGREGATE)
    # Dormant underwater components — registered + pluggable, off by default.
    reg.register(CatchComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    reg.register(PullComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    reg.register(FlutterKickComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    return reg
