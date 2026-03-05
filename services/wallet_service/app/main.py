"""FastAPI application for the Wallet Service."""

from fastapi import FastAPI

from services.wallet_service.routers.admin import router as admin_router
from services.wallet_service.routers.internal import router as internal_router
from services.wallet_service.routers.member import router as wallet_router
from services.wallet_service.routers.referral import router as referral_router
from services.wallet_service.routers.referral_admin import (
    router as referral_admin_router,
)
from services.wallet_service.routers.rewards_admin import router as rewards_admin_router
from services.wallet_service.routers.rewards_internal import (
    router as rewards_internal_router,
)
from services.wallet_service.routers.rewards_member import (
    router as rewards_member_router,
)


def create_app() -> FastAPI:
    """Create and configure the Wallet Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Wallet Service",
        version="0.1.0",
        description="Bubbles wallet and balance management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "wallet"}

    # Member-facing routes
    # Gateway: /api/v1/wallet/{path} → /wallet/{path}
    app.include_router(wallet_router)

    # Admin routes
    # Gateway: /api/v1/admin/wallet/{path} → /admin/wallet/{path}
    app.include_router(admin_router)

    # Referral routes (member-facing)
    # Gateway: /api/v1/wallet/referral/{path} → /wallet/referral/{path}
    app.include_router(referral_router)

    # Referral admin routes
    # Gateway: /api/v1/admin/wallet/referrals/{path} → /admin/wallet/referrals/{path}
    app.include_router(referral_admin_router)

    # Rewards member-facing routes
    # Gateway: /api/v1/wallet/rewards/{path} → /wallet/rewards/{path}
    app.include_router(rewards_member_router)

    # Rewards admin routes
    # Gateway: /api/v1/admin/wallet/rewards/{path} → /admin/wallet/rewards/{path}
    app.include_router(rewards_admin_router)

    # Internal service-to-service routes (not proxied by gateway)
    app.include_router(internal_router)
    app.include_router(rewards_internal_router)

    return app


app = create_app()
