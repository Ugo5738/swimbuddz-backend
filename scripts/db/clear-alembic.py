import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


# Load .env.prod manually
def load_env_prod():
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.prod"
    )
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value


load_env_prod()


async def clear_alembic_version():
    db_url = os.environ.get("DATABASE_URL")
    print("Connecting to database...")

    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            print("Clearing alembic_version table...")
            await conn.execute(text("DELETE FROM alembic_version;"))
            print("Alembic version table cleared.")

    except Exception as e:
        print(f"Error clearing alembic version: {e}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(clear_alembic_version())
