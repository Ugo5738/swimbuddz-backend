"""Shared health-check endpoint factory.

Every backend service exposes the same ``GET /health`` shape so the load
balancer and Docker compose health checks can poll the same path with the
same expected payload. Use :func:`register_health_check` instead of
defining the handler inline.

Usage::

    from libs.common.health import register_health_check

    app = FastAPI(title="…")
    register_health_check(app, "chat")
"""

from __future__ import annotations

from fastapi import FastAPI


def register_health_check(app: FastAPI, service_name: str) -> None:
    """Attach a standard ``/health`` endpoint to ``app``.

    Responds with ``{"status": "ok", "service": "<service_name>"}`` and a
    200 status. Tagged ``system`` so OpenAPI groups it out of the
    domain-route view.
    """

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "service": service_name}


__all__ = ["register_health_check"]
