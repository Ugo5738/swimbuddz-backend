"""Media Service main application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from libs.common.config import get_settings
from services.media_service.routers.albums import router as albums_router
from services.media_service.routers.assets import router as assets_router
from services.media_service.routers.media import router as media_router

settings = get_settings()

app = FastAPI(title="SwimBuddz Media Service")

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

# Include routers
app.include_router(albums_router)
app.include_router(media_router)
app.include_router(assets_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "media"}
