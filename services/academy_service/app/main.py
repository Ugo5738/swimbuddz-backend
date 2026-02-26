"""FastAPI application for the Academy Service."""

from fastapi import FastAPI
from services.academy_service.routers.admin_tasks import router as admin_tasks_router
from services.academy_service.routers.coach_assignment import (
    router as assignment_router,
)
from services.academy_service.routers.coach_dashboard import (
    router as coach_dashboard_router,
)
from services.academy_service.routers.cohorts import router as cohorts_router
from services.academy_service.routers.curriculum import router as curriculum_router
from services.academy_service.routers.enrollments import router as enrollments_router
from services.academy_service.routers.internal import router as internal_router
from services.academy_service.routers.programs import router as programs_router
from services.academy_service.routers.progress import router as progress_router
from services.academy_service.routers.reports import router as reports_router
from services.academy_service.routers.scoring import router as scoring_router


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

    # Domain routers (all prefixed /academy)
    app.include_router(programs_router, prefix="/academy")
    app.include_router(cohorts_router, prefix="/academy")
    app.include_router(enrollments_router, prefix="/academy")
    app.include_router(progress_router, prefix="/academy")
    app.include_router(coach_dashboard_router, prefix="/academy")
    app.include_router(scoring_router, prefix="/academy")
    app.include_router(reports_router, prefix="/academy")
    app.include_router(admin_tasks_router, prefix="/academy")
    app.include_router(internal_router, prefix="/academy")

    # Shared routers (curriculum and coach assignments)
    app.include_router(curriculum_router, prefix="/academy")
    app.include_router(assignment_router, prefix="/academy")

    return app


app = create_app()
