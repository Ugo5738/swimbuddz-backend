"""FastAPI application for the Volunteer Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.volunteer_service.routers.admin import router as admin_router
from services.volunteer_service.routers.internal import router as internal_router
from services.volunteer_service.routers.member import router as volunteer_router


def create_app() -> FastAPI:
    """Create and configure the Volunteer Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Volunteer Service",
        version="0.1.0",
        description="Volunteer management service for SwimBuddz — roles, opportunities, scheduling, hours tracking, and rewards.",
    )

    register_health_check(app, "volunteer")

    app.include_router(volunteer_router)
    app.include_router(admin_router)
    app.include_router(internal_router)

    return app


app = create_app()
