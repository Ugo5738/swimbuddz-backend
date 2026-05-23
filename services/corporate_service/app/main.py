"""FastAPI application for the Corporate Wellness Service."""

from fastapi import FastAPI
from slowapi.errors import RateLimitExceeded

from libs.common.health import register_health_check
from libs.common.rate_limit import limiter, rate_limit_exceeded_handler
from services.corporate_service.routers import (
    admin_contacts_router,
    admin_deals_router,
    admin_employees_router,
    admin_orchestration_router,
    admin_outreach_router,
    admin_programs_router,
    admin_reports_router,
    admin_touchpoints_router,
    me_auth_router,
    me_programs_router,
    public_router,
)


def create_app() -> FastAPI:
    """Create and configure the Corporate Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Corporate Service",
        version="0.1.0",
        description=(
            "Corporate wellness sales pipeline (contacts, deals, touchpoints) "
            "and program orchestration (cohort linking, wallet provisioning, "
            "bulk employee enrollment)."
        ),
    )

    # Rate limiter wiring — required by slowapi's @limiter.limit decorator on
    # the public lead-capture endpoint. Without this, slowapi tries to read
    # app.state.limiter and explodes the first time someone POSTs.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    register_health_check(app, "corporate")

    # Public router — no auth, rate-limited. Powers the marketing site
    # intake form at swimbuddz.com/corporate.
    app.include_router(public_router, prefix="/corporate")

    # HR portal — magic-link auth + read-only company-scoped views.
    # ``me_auth`` is mounted before ``me_programs`` so the /me/auth/*
    # routes resolve before the auth-protected /me/programs/* ones.
    app.include_router(me_auth_router, prefix="/corporate")
    app.include_router(me_programs_router, prefix="/corporate")

    # Admin-only routers — gateway exposes everything under /admin/corporate/*.
    # Touchpoints + deals are children of contacts; declared before deals/programs
    # so /admin/corporate/contacts/{id}/touchpoints isn't shadowed.
    app.include_router(admin_touchpoints_router, prefix="/admin/corporate")
    app.include_router(admin_deals_router, prefix="/admin/corporate")
    app.include_router(admin_employees_router, prefix="/admin/corporate")
    app.include_router(admin_orchestration_router, prefix="/admin/corporate")
    app.include_router(admin_outreach_router, prefix="/admin/corporate")
    # Reports must come BEFORE the generic programs router so
    # /programs/{id}/report doesn't get swallowed by /programs/{id}.
    app.include_router(admin_reports_router, prefix="/admin/corporate")
    app.include_router(admin_programs_router, prefix="/admin/corporate")
    app.include_router(admin_contacts_router, prefix="/admin/corporate")

    return app


app = create_app()
