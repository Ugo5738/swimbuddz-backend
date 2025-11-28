import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine


def load_env_file() -> None:
    """Load environment variables for DB credentials.

    Uses ENV_FILE (defaults to .env.prod for resets) relative to project root so
    every step in the reset pipeline targets the same database.
    """

    project_root = Path(__file__).resolve().parents[2]
    env_file = os.environ.get("ENV_FILE", ".env.prod")
    env_path = (project_root / env_file).resolve()

    if not env_path.exists():
        raise FileNotFoundError(f"Env file not found: {env_path}")

    load_dotenv(env_path, override=True)


load_env_file()

def database_url_candidates() -> list[tuple[str, str]]:
    """Return DB URL candidates in priority order.

    Tries direct > transaction > pooler, but we'll fall back if connection fails.
    """

    candidates: list[tuple[str, str]] = []
    for key in ("DATABASE_DIRECT_URL", "DATABASE_TRANSACTION_URL", "DATABASE_URL"):
        url = os.environ.get(key)
        if url:
            candidates.append((key, url))
    if not candidates:
        raise ValueError("No database URL found (expected DATABASE_DIRECT_URL/DATABASE_TRANSACTION_URL/DATABASE_URL)")
    return candidates

async def nuke_tables():
    candidates = database_url_candidates()
    last_error: Exception | None = None

    for key, url in candidates:
        print("\nNuking database...")

        # Mask password for logging
        masked_url = url
        if '@' in masked_url:
            try:
                scheme_part, host_part = masked_url.rsplit('@', 1)
                if ':' in scheme_part:
                    scheme_user, _ = scheme_part.rsplit(':', 1)
                    masked_url = f"{scheme_user}:***@{host_part}"
            except Exception:
                pass

        print(f"Connecting with {key}: {masked_url}")

        engine = create_async_engine(url, echo=False, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                def nuke(sync_conn):
                    # Disable statement timeout; keep lock timeout short so we don't hang
                    lock_timeout_ms = int(os.environ.get("NUKE_LOCK_TIMEOUT_MS", "5000"))
                    sync_conn.execute(text("SET SESSION statement_timeout = 0;"))
                    sync_conn.execute(text(f"SET lock_timeout = {lock_timeout_ms};"))

                    # Gather tables in public
                    result = sync_conn.execute(text("""
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename;
                    """))
                    tables = [row[0] for row in result]

                    if not tables:
                        print("No tables found in public schema. Nothing to drop.")
                    else:
                        print(f"Found {len(tables)} tables to drop:")
                        for i, t in enumerate(tables, 1):
                            print(f"  {i}. {t}")

                        print("\nDropping tables individually (CASCADE)...")
                        dropped = 0
                        for i, t in enumerate(tables, 1):
                            try:
                                print(f"  [{i}/{len(tables)}] {t}...", end=" ", flush=True)
                                sync_conn.execute(text(f"DROP TABLE IF EXISTS \"{t}\" CASCADE;"))
                                print("✓")
                                dropped += 1
                            except Exception as ex:
                                print(f"✗ {str(ex)[:120]}")

                        print(f"\n✓ Dropped {dropped}/{len(tables)} tables (remaining may be locked)")

                    # Drop enums in public schema to avoid duplicate type errors
                    result_types = sync_conn.execute(text("""
                        SELECT n.nspname AS schema, t.typname AS type_name
                        FROM pg_type t
                        JOIN pg_namespace n ON n.oid = t.typnamespace
                        WHERE t.typtype = 'e' AND n.nspname = 'public'
                        ORDER BY type_name;
                    """))
                    enum_types = [(row[0], row[1]) for row in result_types]

                    if enum_types:
                        print("\nDropping enum types in public schema...")
                        for schema, type_name in enum_types:
                            try:
                                print(f"  Dropping type {schema}.{type_name}...", end=" ", flush=True)
                                sync_conn.execute(text(f"DROP TYPE IF EXISTS \"{schema}\".\"{type_name}\" CASCADE;"))
                                print("✓")
                            except Exception as ex:
                                print(f"✗ {str(ex)[:120]}")
                    else:
                        print("No enum types found in public schema.")

                    # Ensure schema still exists for migrations
                    sync_conn.execute(text("CREATE SCHEMA IF NOT EXISTS public;"))
                    sync_conn.execute(text("GRANT ALL ON SCHEMA public TO public;"))
                    print("Schema ready for migrations.")
                
                await conn.run_sync(nuke)
                # Success, stop trying other URLs
                return

        except OperationalError as e:
            print(f"Connection failed with {key}: {e}")
            last_error = e
        except Exception as e:
            print(f"Error nuking database with {key}: {e}")
            last_error = e
        finally:
            await engine.dispose()

    if last_error:
        raise last_error

if __name__ == "__main__":
    asyncio.run(nuke_tables())
