import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

# =============================================================================
# SAFETY CONSTANTS
# =============================================================================

# Keywords that indicate a production database - refuse to nuke without confirmation
PRODUCTION_INDICATORS = [
    "prod",
    "production",
    "live",
    "main",
]


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
        raise ValueError(
            "No database URL found (expected DATABASE_DIRECT_URL/DATABASE_TRANSACTION_URL/DATABASE_URL)"
        )
    return candidates


def extract_database_info(url: str) -> dict:
    """Extract readable info from database URL for display."""
    info = {
        "host": "unknown",
        "project_id": "unknown",
        "region": "unknown",
    }

    try:
        # Extract host part after @
        if "@" in url:
            host_part = url.split("@")[1].split("/")[0].split(":")[0]
            info["host"] = host_part

            # Extract Supabase project ID (format: postgres.PROJECT_ID@...)
            if "postgres." in url:
                project_part = url.split("postgres.")[1].split(":")[0].split("@")[0]
                info["project_id"] = project_part

            # Detect region from host
            if "eu-west-1" in host_part:
                info["region"] = "eu-west-1 (Ireland)"
            elif "eu-central-1" in host_part:
                info["region"] = "eu-central-1 (Frankfurt)"
            elif "us-east-1" in host_part:
                info["region"] = "us-east-1 (N. Virginia)"
            elif "us-west-1" in host_part:
                info["region"] = "us-west-1 (N. California)"
    except Exception:
        pass

    return info


def is_production_database() -> bool:
    """Check if the current environment indicates production."""

    # Check ENVIRONMENT variable
    env = os.environ.get("ENVIRONMENT", "").lower()
    if any(indicator in env for indicator in PRODUCTION_INDICATORS):
        return True

    # Check ENV_FILE variable
    env_file = os.environ.get("ENV_FILE", "").lower()
    if any(indicator in env_file for indicator in PRODUCTION_INDICATORS):
        return True

    # Check database URL for production indicators
    for _, url in database_url_candidates():
        url_lower = url.lower()
        if any(indicator in url_lower for indicator in PRODUCTION_INDICATORS):
            return True

    return False


def mask_url(url: str) -> str:
    """Mask password in URL for safe logging."""
    masked_url = url
    if "@" in masked_url:
        try:
            scheme_part, host_part = masked_url.rsplit("@", 1)
            if ":" in scheme_part:
                scheme_user, _ = scheme_part.rsplit(":", 1)
                masked_url = f"{scheme_user}:***@{host_part}"
        except Exception:
            pass
    return masked_url


def print_database_warning():
    """Print a prominent warning about which database will be destroyed."""

    env_file = os.environ.get("ENV_FILE", "unknown")
    environment = os.environ.get("ENVIRONMENT", "unknown")
    candidates = database_url_candidates()

    # Get info from first candidate URL
    db_info = extract_database_info(candidates[0][1]) if candidates else {}

    print("\n" + "=" * 70)
    print("⚠️  DATABASE DESTRUCTION WARNING ⚠️")
    print("=" * 70)
    print(f"\n  Environment file : {env_file}")
    print(f"  ENVIRONMENT var  : {environment}")
    print(f"  Project ID       : {db_info.get('project_id', 'unknown')}")
    print(f"  Region           : {db_info.get('region', 'unknown')}")
    print(f"  Host             : {db_info.get('host', 'unknown')}")
    print("\n  Database URL(s):")
    for key, url in candidates:
        print(f"    {key}: {mask_url(url)}")
    print("\n" + "=" * 70)


def require_confirmation(is_prod: bool) -> bool:
    """Require user confirmation before nuking.

    Returns True if user confirms, False otherwise.
    """

    # Check for --force flag to skip confirmation (for automated scripts)
    if "--force" in sys.argv:
        if is_prod:
            print("\n❌ ERROR: Cannot use --force on production database!")
            print("   Production databases require interactive confirmation.")
            return False
        print("\n⚡ Skipping confirmation (--force flag detected)")
        return True

    # Check for --yes flag (non-production only)
    if "--yes" in sys.argv or "-y" in sys.argv:
        if is_prod:
            print("\n❌ ERROR: Cannot use --yes on production database!")
            print("   Production databases require typing the confirmation phrase.")
            return False
        print("\n⚡ Skipping confirmation (--yes flag detected)")
        return True

    if is_prod:
        print("\n🚨 THIS APPEARS TO BE A PRODUCTION DATABASE! 🚨")
        print("\nTo proceed, type exactly: DESTROY PRODUCTION DATA")
        print("(or press Ctrl+C to abort)\n")

        try:
            response = input("Confirmation: ").strip()
            if response == "DESTROY PRODUCTION DATA":
                print("\n⚠️  Proceeding with production database destruction...")
                return True
            else:
                print("\n❌ Confirmation phrase did not match. Aborting.")
                return False
        except (KeyboardInterrupt, EOFError):
            print("\n\n❌ Aborted by user.")
            return False
    else:
        print("\nThis will DROP ALL TABLES in this database.")
        print("Type 'yes' to confirm (or press Ctrl+C to abort): ", end="")

        try:
            response = input().strip().lower()
            if response == "yes":
                return True
            else:
                print("\n❌ Aborting.")
                return False
        except (KeyboardInterrupt, EOFError):
            print("\n\n❌ Aborted by user.")
            return False


async def nuke_tables():
    candidates = database_url_candidates()
    last_error: Exception | None = None

    for key, url in candidates:
        print("\nNuking database...")
        print(f"Connecting with {key}: {mask_url(url)}")

        engine = create_async_engine(url, echo=False, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:

                def nuke(sync_conn):
                    # Disable statement timeout; keep lock timeout short so we don't hang
                    lock_timeout_ms = int(
                        os.environ.get("NUKE_LOCK_TIMEOUT_MS", "5000")
                    )
                    sync_conn.execute(text("SET SESSION statement_timeout = 0;"))
                    sync_conn.execute(text(f"SET lock_timeout = {lock_timeout_ms};"))

                    # Gather tables in public
                    result = sync_conn.execute(
                        text(
                            """
                        SELECT tablename FROM pg_tables
                        WHERE schemaname = 'public'
                        ORDER BY tablename;
                    """
                        )
                    )
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
                                print(
                                    f"  [{i}/{len(tables)}] {t}...", end=" ", flush=True
                                )
                                sync_conn.execute(
                                    text(f'DROP TABLE IF EXISTS "{t}" CASCADE;')
                                )
                                print("✓")
                                dropped += 1
                            except Exception as ex:
                                print(f"✗ {str(ex)[:120]}")

                        print(
                            f"\n✓ Dropped {dropped}/{len(tables)} tables (remaining may be locked)"
                        )

                    # Drop enums in public schema to avoid duplicate type errors
                    result_types = sync_conn.execute(
                        text(
                            """
                        SELECT n.nspname AS schema, t.typname AS type_name
                        FROM pg_type t
                        JOIN pg_namespace n ON n.oid = t.typnamespace
                        WHERE t.typtype = 'e' AND n.nspname = 'public'
                        ORDER BY type_name;
                    """
                        )
                    )
                    enum_types = [(row[0], row[1]) for row in result_types]

                    if enum_types:
                        print("\nDropping enum types in public schema...")
                        for schema, type_name in enum_types:
                            try:
                                print(
                                    f"  Dropping type {schema}.{type_name}...",
                                    end=" ",
                                    flush=True,
                                )
                                sync_conn.execute(
                                    text(
                                        f'DROP TYPE IF EXISTS "{schema}"."{type_name}" CASCADE;'
                                    )
                                )
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


def main():
    """Main entry point with safety checks."""

    # Print database warning
    print_database_warning()

    # Check if production
    is_prod = is_production_database()

    # Require confirmation
    if not require_confirmation(is_prod):
        sys.exit(1)

    # Run the nuke
    asyncio.run(nuke_tables())


if __name__ == "__main__":
    main()
