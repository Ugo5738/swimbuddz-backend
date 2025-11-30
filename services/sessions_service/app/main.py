"""FastAPI application for the Sessions Service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.sessions_service.router import router as sessions_router
from services.sessions_service.template_router import router as templates_router


def create_app() -> FastAPI:
    """Create and configure the Sessions Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Sessions Service",
        version="0.1.0",
        description="Session management service for SwimBuddz.",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "https://swimbuddz.com",
            "https://www.swimbuddz.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "sessions"}

    # Include routers
    # Register templates router with  full path to avoid trailing slash issues
    # FastAPI is strict about trailing slashes - /sessions/templates != /sessions/templates/
    app.include_router(templates_router)
    app.include_router(sessions_router)

    return app


app = create_app()
