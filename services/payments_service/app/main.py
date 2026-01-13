"""FastAPI application for the Payments Service."""

from fastapi import FastAPI
from services.payments_service.payout_router import admin_router as payout_admin_router
from services.payments_service.payout_router import coach_router as payout_coach_router
from services.payments_service.router import router as payments_router


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

    # Include payments router
    app.include_router(payments_router)

    # Include payout routers for coach payout management
    app.include_router(payout_admin_router)
    app.include_router(payout_coach_router)

    return app


app = create_app()
