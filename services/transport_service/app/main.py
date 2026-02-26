"""FastAPI application for the Transport Service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.transport_service.routers.areas import router as areas_router
from services.transport_service.routers.bookings import router as bookings_router
from services.transport_service.routers.routes import router as routes_router


def create_app() -> FastAPI:
    """Create and configure the Transport Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Transport Service",
        version="0.1.0",
        description="Transport and ride logistics service for SwimBuddz.",
    )

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
        return {"status": "ok", "service": "transport"}

    app.include_router(areas_router)
    app.include_router(routes_router)
    app.include_router(bookings_router)

    return app


app = create_app()
