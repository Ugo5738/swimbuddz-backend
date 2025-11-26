"""Media Service main application."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from libs.db.session import engine
from .models import Base
from .router import router

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="SwimBuddz Media Service")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly in production
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
