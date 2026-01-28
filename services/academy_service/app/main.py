"""FastAPI application for the Academy Service."""

from fastapi import FastAPI
from services.academy_service.curriculum_router import router as curriculum_router
from services.academy_service.router import router as academy_router


def create_app() -> FastAPI:
    """Create and configure the Academy Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Academy Service",
        version="0.1.0",
        description="Academy management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "academy"}

    # Include academy router
    app.include_router(academy_router, prefix="/academy")

    # Include curriculum router (skills, weeks, lessons)
    app.include_router(curriculum_router, prefix="/academy")

    return app


app = create_app()
