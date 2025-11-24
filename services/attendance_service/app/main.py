"""FastAPI application for the Attendance Service."""
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

from services.attendance_service.router import router as attendance_router


def create_app() -> FastAPI:
    """Create and configure the Attendance Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Attendance Service",
        version="0.1.0",
        description="Attendance tracking service for SwimBuddz.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "attendance"}

    # Include attendance router
    app.include_router(attendance_router, prefix="/attendance")

    return app


app = create_app()
