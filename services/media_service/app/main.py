"""Media Service main application."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.media_service.router import router
from libs.common.config import get_settings

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

# Include router
app.include_router(router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "media"}
