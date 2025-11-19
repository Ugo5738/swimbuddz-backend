"""FastAPI application entrypoint for the SwimBuddz gateway service."""
from __future__ import annotations

from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="SwimBuddz Gateway Service",
        version="0.1.0",
        description="Backend-for-frontend that orchestrates SwimBuddz domain services.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:  # pragma: no cover - trivial wiring
        """Simple readiness endpoint used during bootstrap."""
        return {"status": "ok"}

    return app


app = create_app()
