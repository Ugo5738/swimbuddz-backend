"""FastAPI application for the Communications Service."""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from services.communications_service.router import router as communications_router
from services.communications_service.router import content_router


def create_app() -> FastAPI:
    """Create and configure the Communications Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Communications Service",
        version="0.1.0",
        description="Communications and announcements service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "communications"}

    # Include communications routers
    app.include_router(communications_router)
    app.include_router(content_router)

    return app


app = create_app()
