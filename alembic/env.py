import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import our application config and models
from libs.common.config import get_settings
from libs.db.base import Base
# Import all models here so they are registered with Base.metadata
from services.members_service.models import Member  # noqa: F401
from services.members_service.models import PendingRegistration  # noqa: F401
from services.members_service.models import VolunteerRole, VolunteerInterest, ClubChallenge, MemberChallengeCompletion  # noqa: F401
from services.sessions_service.models import Session  # noqa: F401
from services.attendance_service.models import SessionAttendance  # noqa: F401
from services.communications_service.models import Announcement  # noqa: F401
from services.payments_service.models import Payment  # noqa: F401
from services.academy_service.models import Program, Cohort, Enrollment, Milestone, StudentProgress  # noqa: F401

settings = get_settings()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = None
target_metadata = Base.metadata

# Override sqlalchemy.url with our settings
# Override sqlalchemy.url with our settings
url = settings.DATABASE_URL.replace("%", "%%")
config.set_main_option("sqlalchemy.url", url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Create engine from our settings, but we can use alembic's config structure if we want
    # or just use our own engine. The template uses async_engine_from_config.
    # Let's use our settings URL but keep the config object for other options.
    
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
