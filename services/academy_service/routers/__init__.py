"""Academy service routers."""

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

__all__ = [
    "admin_tasks_router",
    "assignment_router",
    "coach_dashboard_router",
    "cohorts_router",
    "curriculum_router",
    "enrollments_router",
    "internal_router",
    "programs_router",
    "progress_router",
    "reports_router",
    "scoring_router",
]
