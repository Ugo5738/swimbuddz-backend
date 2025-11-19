import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from libs.common.config import get_settings
from libs.db.base import Base

settings = get_settings()

@pytest.fixture(scope="session")
def event_loop():
    """
    Create an instance of the default event loop for the session.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """
    Create a test engine that connects to the database.
    In a real scenario, we might want to create a separate test DB.
    For now, we'll use the main DB but wrap tests in transactions.
    """
    engine = create_async_engine(settings.DATABASE_URL, future=True)
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    yield engine
    
    # Drop tables after session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session that rolls back after the test.
    """
    connection = await test_engine.connect()
    transaction = await connection.begin()
    
    session_factory = async_sessionmaker(bind=connection, class_=AsyncSession)
    session = session_factory()
    
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
