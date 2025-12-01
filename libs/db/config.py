from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from libs.common.config import get_settings

settings = get_settings()

# Create async engine
# echo=True for local dev to see SQL queries
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=(settings.ENVIRONMENT == "local"),
    future=True,
    pool_pre_ping=True,  # Test connections before using
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)
