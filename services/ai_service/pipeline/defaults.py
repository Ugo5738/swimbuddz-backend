"""THE pipeline control surface — the stage flow + per-component on/off.

This is the one file to read to understand the flow and flip pieces on/off.

    FLOW (per clip):
      gate            ALWAYS   3-tier view/usability decision; REFUSE short-circuits
                               the whole pipeline before any coaching cost.
      phase_segment   Stage 1  STROKELAB_COACH_SEGMENT   classify EVERY frame (one VLM
                               call: phase + arm + recovery sub-phase, all stored) and
                               group into ctx.instances (every visible phase, both arms).
                               No counting, no coaching here.
      recovery_coach  Stage 2  STROKELAB_COACH_RECOVERY  coach a representative near-arm
                               recovery (needs phase_segment to have run first).
      holistic_coach  Stage 2  STROKELAB_COACH_HOLISTIC  whole-clip coaching (independent).
      collate         Stage 3  STROKELAB_COACH_COLLATE   derive counts/metrics from
                               ctx.instances → the hedged "~N recoveries" summary.
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

from services.ai_service.pipeline.components.collate import CollateComponent
from services.ai_service.pipeline.components.gate import GateComponent
from services.ai_service.pipeline.components.holistic_coach import (
    HolisticCoachComponent,
)
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


def build_default_registry() -> Registry:
    """Register the components in flow order, honouring the config toggles."""
    from libs.common.config import get_settings

    s = get_settings()
    reg = Registry()
    reg.register(
        GateComponent()
    )  # IS_GATE — always; runs first, sets the 3-tier branch
    reg.register(PhaseSegmentComponent(), enabled=s.STROKELAB_COACH_SEGMENT)
    reg.register(RecoveryCoachComponent(), enabled=s.STROKELAB_COACH_RECOVERY)
    reg.register(HolisticCoachComponent(), enabled=s.STROKELAB_COACH_HOLISTIC)
    reg.register(CollateComponent(), enabled=s.STROKELAB_COACH_COLLATE)
    # Dormant underwater components — registered + pluggable, off by default.
    reg.register(CatchComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    reg.register(PullComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    reg.register(FlutterKickComponent(), enabled=s.STROKELAB_COACH_UNDERWATER)
    return reg
