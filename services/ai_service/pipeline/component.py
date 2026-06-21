"""The Component interface — the one contract every analyzer implements.

A component declares WHAT it needs (``consumes`` phase, ``granularity``) and
WHEN it applies (``available`` for a given input profile), and implements
``run`` to turn the context into ``Finding``s. Keep components pure-ish: read the
``RunContext``, return a ``ComponentResult`` — no global state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from services.ai_service.pipeline.types import (
    ComponentResult,
    Granularity,
    InputProfile,
    Phase,
    RunContext,
)


class Component(ABC):
    """Base class for all pipeline components.

    Subclasses set the class attributes and implement ``run``. ``IS_GATE`` marks
    the single component the runner consults first to decide the 3-tier branch.
    """

    name: str = "component"
    consumes: Phase = Phase.CLIP
    granularity: Granularity = Granularity.FRAME
    #: which input profiles this component can be assessed from
    profiles: tuple[InputProfile, ...] = (
        InputProfile.SIDE_ON_ABOVE,
        InputProfile.UNKNOWN,
    )
    #: True only for the gate component (runner treats it specially)
    IS_GATE: bool = False
    #: shown when ``available()`` is False — override for a specific honest message
    unavailable_reason: str = "Not assessable from this footage."
    #: closed-enum UX bucket for the unavailable finding (body_line, catch_pull, …)
    unavailable_area: Optional[str] = None

    def available(self, profile: InputProfile) -> bool:
        """Can this component be honestly assessed from this footage?

        Default: available for the declared ``profiles``. Override for richer
        logic. A False here makes the runner emit an honest "can't see this from
        this footage" finding instead of running the component.
        """
        return profile in self.profiles

    @abstractmethod
    async def run(self, ctx: RunContext) -> ComponentResult:
        """Analyze the context and return findings + telemetry."""
        raise NotImplementedError
