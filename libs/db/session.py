from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from libs.db.config import AsyncSessionLocal


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
