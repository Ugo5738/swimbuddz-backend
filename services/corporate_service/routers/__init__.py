"""Corporate service routers package."""

from services.corporate_service.routers.admin_contacts import router as admin_contacts_router
from services.corporate_service.routers.admin_deals import router as admin_deals_router
from services.corporate_service.routers.admin_employees import (
    router as admin_employees_router,
)
from services.corporate_service.routers.admin_orchestration import (
    router as admin_orchestration_router,
)
from services.corporate_service.routers.admin_outreach import (
    router as admin_outreach_router,
)
from services.corporate_service.routers.admin_programs import (
    router as admin_programs_router,
)
from services.corporate_service.routers.admin_reports import (
    router as admin_reports_router,
)
from services.corporate_service.routers.admin_touchpoints import (
    router as admin_touchpoints_router,
)
from services.corporate_service.routers.me_auth import router as me_auth_router
from services.corporate_service.routers.me_programs import (
    router as me_programs_router,
)
from services.corporate_service.routers.public import router as public_router

__all__ = [
    "admin_contacts_router",
    "admin_deals_router",
    "admin_employees_router",
    "admin_orchestration_router",
    "admin_outreach_router",
    "admin_programs_router",
    "admin_reports_router",
    "admin_touchpoints_router",
    "me_auth_router",
    "me_programs_router",
    "public_router",
]
