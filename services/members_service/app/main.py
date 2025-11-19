"""FastAPI application for the Members Service."""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from services.members_service.router import router as members_router
from services.members_service.router import pending_router


def create_app() -> FastAPI:
    """Create and configure the Members Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Members Service",
        version="0.1.0",
        description="Member management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "members"}

    # Include member routers
    app.include_router(members_router)
    app.include_router(pending_router)

    return app


app = create_app()
