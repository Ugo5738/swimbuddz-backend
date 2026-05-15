"""FastAPI application for the Events Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.events_service.routers.member import router as events_router


def create_app() -> FastAPI:
    """Create and configure the Events Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Events Service",
        version="0.1.0",
        description="Community events and RSVP management service for SwimBuddz.",
    )

    register_health_check(app, "events")

    # Include events router
    app.include_router(events_router)

    return app


app = create_app()
