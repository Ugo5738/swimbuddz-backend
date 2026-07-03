"""Dormant underwater-only components — catch, pull, flutter-kick.

These three phases happen BELOW the surface and cannot be honestly assessed from
the side-on, above-water footage Stroke Lab takes today (the visibility taxonomy
in the design doc). They are real, registered, pluggable components — but
``profiles=(UNDERWATER,)`` so on above-water clips the runner emits each one's
honest ``unavailable_reason`` card ("can't see this from this footage") instead of
running it. They stay registered-but-disabled (dormant) until an underwater input
profile + analyzer exist; flip ``STROKELAB_COACH_UNDERWATER`` to surface the
honest-gap cards, and implement ``run`` when underwater analysis lands.
"""

from __future__ import annotations

from services.ai_service.pipeline.component import Component
from services.ai_service.pipeline.types import (
    SEVERITY_UNAVAILABLE,
    ComponentResult,
    Finding,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)


class _UnderwaterComponent(Component):
    """Base for underwater-only analyzers. ``run`` is reached only on UNDERWATER
    footage (which we don't analyze yet) — so it returns the honest dormant card."""

    granularity = Granularity.CHUNK
    profiles = (InputProfile.UNDERWATER,)

    async def run(self, ctx: RunContext) -> ComponentResult:
        return ComponentResult(
            self.name,
            [
                Finding(
                    component=self.name,
                    observation=self.unavailable_reason,
                    severity=SEVERITY_UNAVAILABLE,
                    available=False,
                    confidence=0.0,
                    area=self.unavailable_area,
                )
            ],
            meta={"dormant": True},
        )


class CatchComponent(_UnderwaterComponent):
    name = "catch"
    consumes = Phase.CATCH
    unavailable_reason = (
        "The catch is an underwater anchoring skill — film from below the surface "
        "or work with a coach in the pool before judging it."
    )
    unavailable_area = "catch_pull"


class PullComponent(_UnderwaterComponent):
    name = "pull"
    consumes = Phase.PULL
    unavailable_reason = (
        "The underwater pull path is hidden from an above-water side angle, so I "
        "won't guess at it from this clip."
    )
    unavailable_area = "catch_pull"


class FlutterKickComponent(_UnderwaterComponent):
    name = "flutter_kick"
    consumes = Phase.CLIP  # the kick runs through the whole cycle (no dedicated phase)
    unavailable_reason = (
        "Kick timing and depth need clearer underwater or head-on footage; from "
        "this view, quiet-leg rhythm would be a guess."
    )
    unavailable_area = "kick"
