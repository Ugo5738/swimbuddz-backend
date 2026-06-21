"""Component registry — registration + the on/off toggles.

Default is every registered component enabled; callers flip individual ones off
(e.g. run Stage-1/gate only, or disable the expensive coach). Order is preserved
so the runner can rely on a stable component order.
"""

from __future__ import annotations

from services.ai_service.pipeline.component import Component


class Registry:
    def __init__(self) -> None:
        self._components: list[Component] = []
        self._enabled: dict[str, bool] = {}

    def register(self, component: Component, *, enabled: bool = True) -> "Registry":
        if any(c.name == component.name for c in self._components):
            raise ValueError(f"component already registered: {component.name}")
        self._components.append(component)
        self._enabled[component.name] = enabled
        return self

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._enabled:
            raise KeyError(name)
        self._enabled[name] = enabled

    def is_enabled(self, name: str) -> bool:
        return self._enabled.get(name, False)

    def get(self, name: str) -> Component:
        for c in self._components:
            if c.name == name:
                return c
        raise KeyError(name)

    def gate(self) -> Component | None:
        return next((c for c in self._components if c.IS_GATE), None)

    def analysis_components(self, *, enabled_only: bool = True) -> list[Component]:
        """Non-gate components, in registration order."""
        return [
            c
            for c in self._components
            if not c.IS_GATE and (not enabled_only or self._enabled[c.name])
        ]
