"""FastAPI application for the Ledger Service.

Multi-tenant double-entry accounting — the source of truth for money across
SwimBuddz. See docs/design/LEDGER_SERVICE_DESIGN.md and
docs/design/LEDGER_IMPLEMENTATION_PLAN.md.

PR-0 scaffold: app boots with /health and empty routers. Business logic lands
in PR-1+ (schema, posting, roles, reports).
"""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.ledger_service.routers.admin import router as admin_router
from services.ledger_service.routers.internal import router as internal_router
from services.ledger_service.routers.manual import router as manual_router
from services.ledger_service.routers.organizations import router as organizations_router
from services.ledger_service.routers.treasury import router as treasury_router
from services.ledger_service.routers.users import router as users_router


def create_app() -> FastAPI:
    """Create and configure the Ledger Service FastAPI app."""
    app = FastAPI(
        title="Ledger Service",
        version="0.2.0",
        description="Detachable multi-tenant double-entry accounting service.",
    )

    register_health_check(app, "ledger")

    # Admin / finance routes (role-gated: viewer/operations/accountant/admin/owner)
    # Gateway: /api/v1/admin/finance/{path} → /admin/finance/{path}
    app.include_router(admin_router)
    app.include_router(manual_router)
    app.include_router(users_router)
    app.include_router(organizations_router)
    app.include_router(treasury_router)

    # Internal service-to-service routes (not proxied by gateway; service-role JWT)
    app.include_router(internal_router)

    return app


app = create_app()
