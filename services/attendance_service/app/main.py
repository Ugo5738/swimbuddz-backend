"""FastAPI application for the Attendance Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.attendance_service.routers.internal import router as internal_router
from services.attendance_service.routers.member import router as attendance_router


def create_app() -> FastAPI:
    """Create and configure the Attendance Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Attendance Service",
        version="0.1.0",
        description="Attendance tracking service for SwimBuddz.",
    )

    register_health_check(app, "attendance")

    # Include attendance router
    app.include_router(attendance_router, prefix="/attendance")

    # Internal service-to-service endpoints
    app.include_router(internal_router)

    return app


app = create_app()
