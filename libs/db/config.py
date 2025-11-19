from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from libs.common.config import get_settings

settings = get_settings()

# Create async engine
# echo=True for local dev to see SQL queries
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.ENVIRONMENT == "local"),
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)
