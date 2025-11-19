"""FastAPI application for the Sessions Service."""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from services.sessions_service.router import router as sessions_router


def create_app() -> FastAPI:
    """Create and configure the Sessions Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Sessions Service",
        version="0.1.0",
        description="Session management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "sessions"}

    # Include sessions router
    app.include_router(sessions_router)

    return app


app = create_app()
