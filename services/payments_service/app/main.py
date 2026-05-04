"""FastAPI application for the Payments Service."""

from fastapi import FastAPI
from services.payments_service.routers import (
    discounts_router,
    intents_router,
    internal_router,
    manual_router,
    webhooks_router,
)
from services.payments_service.routers.payout import admin_router as payout_admin_router
from services.payments_service.routers.payout import coach_router as payout_coach_router
from services.payments_service.routers.recurring_payout import (
    admin_router as recurring_payout_admin_router,
)
from services.payments_service.routers.recurring_payout import (
    makeups_admin_router as cohort_makeups_admin_router,
)
from services.payments_service.routers.recurring_payout import (
    makeups_coach_router as cohort_makeups_coach_router,
)


def create_app() -> FastAPI:
    """Create and configure the Payments Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Payments Service",
        version="0.1.0",
        description="Payment processing service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "payments"}

    # Include split payments routers
    app.include_router(intents_router)
    app.include_router(webhooks_router)
    app.include_router(discounts_router)
    app.include_router(internal_router)
    app.include_router(manual_router)

    # Include payout routers for coach payout management
    # Mount under /payments prefix to match gateway routing (/api/v1/payments/{path} → /payments/{path})
    app.include_router(payout_admin_router, prefix="/payments")
    app.include_router(payout_coach_router, prefix="/payments")

    # Recurring payout config + make-up obligations
    app.include_router(recurring_payout_admin_router, prefix="/payments")
    app.include_router(cohort_makeups_admin_router, prefix="/payments")
    app.include_router(cohort_makeups_coach_router, prefix="/payments")

    return app


app = create_app()
