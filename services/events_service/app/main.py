"""FastAPI application for the Events Service."""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from services.events_service.router import router as events_router


def create_app() -> FastAPI:
    """Create and configure the Events Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Events Service",
        version="0.1.0",
        description="Community events and RSVP management service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "events"}

    # Include events router
    app.include_router(events_router)

    return app


app = create_app()
