from libs.common.config import get_settings
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

settings = get_settings()

db_url = make_url(settings.DATABASE_URL)
db_host = (db_url.host or "").lower()

# Supabase commonly uses PgBouncer. When using Supabase pooler hosts, avoid
# "pooling a pool" by disabling SQLAlchemy pooling (NullPool) and letting
# PgBouncer handle connection reuse.
use_null_pool = db_host.endswith(".pooler.supabase.com")

connect_args: dict = {}
if db_host.endswith(".supabase.com") or use_null_pool:
    # Be explicit about SSL when connecting to Supabase-managed Postgres.
    connect_args["sslmode"] = "require"
    # Keepalive settings help reduce intermittent SSL EOF disconnects on long-lived
    # pooled connections and NAT-ed networks.
    connect_args["keepalives"] = 1
    connect_args["keepalives_idle"] = 30
    connect_args["keepalives_interval"] = 10
    connect_args["keepalives_count"] = 5

# Create async engine
# echo=True for local dev to see SQL queries
engine_kwargs: dict = {
    "echo": settings.ENVIRONMENT == "local",
    "future": True,
    "pool_pre_ping": True,
    "connect_args": connect_args,
}

if use_null_pool:
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs.update(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,
    )

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)
