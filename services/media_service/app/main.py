"""Media Service main application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from libs.common.config import get_settings
from libs.common.health import register_health_check
from services.media_service.routers.admin_evidence import (
    router as admin_evidence_router,
)
from services.media_service.routers.albums import router as albums_router
from services.media_service.routers.assets import router as assets_router
from services.media_service.routers.audio import router as audio_router
from services.media_service.routers.internal import router as internal_router
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
app.include_router(audio_router)
app.include_router(internal_router)
# Admin-evidence router must be registered after the generic media
# router so that ``/media/admin/...`` doesn't get caught by any
# wildcard rule in ``media_router``.
app.include_router(admin_evidence_router)

register_health_check(app, "media")
