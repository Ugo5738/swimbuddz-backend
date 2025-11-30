"""Clear alembic version table to allow fresh migration"""

import asyncio
from sqlalchemy import text
from libs.db.config import engine


async def clear_alembic_version():
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM alembic_version;"))
        print("✓ Cleared alembic_version table")


if __name__ == "__main__":
    asyncio.run(clear_alembic_version())
