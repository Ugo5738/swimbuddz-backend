import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_db_connection(db_session):
    """
    Test that we can connect to the DB and execute a query.
    """
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1
