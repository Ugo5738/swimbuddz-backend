"""Members service routers package."""

from services.members_service.routers.admin import router as admin_router
from services.members_service.routers.challenges import (
    challenge_router,
    volunteer_router,
)
from services.members_service.routers.coach_agreements import (
    admin_router as coach_agreements_admin_router,
)
from services.members_service.routers.coach_agreements import (
    router as coach_agreements_router,
)
from services.members_service.routers.coach_application import (
    admin_router as coach_application_admin_router,
)
from services.members_service.routers.coach_application import (
    router as coach_application_router,
)
from services.members_service.routers.coach_banking import (
    router as coach_banking_router,
)
from services.members_service.routers.coach_grades import (
    admin_router as coach_grades_admin_router,
)
from services.members_service.routers.coach_grades import router as coach_grades_router
from services.members_service.routers.coaches import router as coaches_router
from services.members_service.routers.internal import router as internal_router
from services.members_service.routers.members import router as members_router
from services.members_service.routers.registration import router as registration_router

__all__ = [
    "registration_router",
    "members_router",
    "coaches_router",
    "admin_router",
    "internal_router",
    "coach_application_router",
    "coach_application_admin_router",
    "coach_banking_router",
    "coach_grades_router",
    "coach_grades_admin_router",
    "coach_agreements_router",
    "coach_agreements_admin_router",
    "challenge_router",
    "volunteer_router",
]
