"""Completely remove a single member from Supabase Auth + the app database.

Use this between registration smoke tests so you can re-register with the
same email. It deletes:

  1. The `pending_registrations` row matching the email (if present — a
     registration that never made it past email confirmation will leave
     one of these behind).
  2. The `members` row matching the email (which cascades to
     member_profiles, member_emergency_contacts, member_availabilities,
     member_memberships, member_preferences, coach_profiles — all of
     those declare `ON DELETE CASCADE`).
  3. The Supabase Auth user matching the email (so re-registration with
     the same email doesn't collide with "user already exists").

Cross-service rows that reference `auth_id` (wallet, payments, etc.) are
intentionally NOT touched. SwimBuddz architecture forbids cross-service
FKs, so those rows are loose by design; for a fresh-registration test
they're irrelevant — the new signup will get a new auth_id anyway. If
you need to clean those up for some specific test, do it per-service.

Usage:
  cd swimbuddz-backend
  ENV_FILE=.env.dev python scripts/auth/clear-member.py user@example.com
  ENV_FILE=.env.dev python scripts/auth/clear-member.py user@example.com --yes  # skip confirm

Safety: refuses to run against env files containing "prod" / "production"
/ "live" / "main" unless `--force-prod` is also passed (and you'll still
get a typed confirmation prompt).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Add backend root to path so we can import libs (auth → scripts → backend root).
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from dotenv import load_dotenv

# Load env BEFORE importing libs that call get_settings(). Default to .env.dev
# (not .env.prod) since this script is intended for local test cleanup.
project_root = Path(__file__).resolve().parents[2]
env_file = os.environ.get("ENV_FILE", ".env.dev")
load_dotenv(project_root / env_file, override=True)

import httpx
from libs.common.config import get_settings
from libs.db.config import AsyncSessionLocal
from sqlalchemy import text

settings = get_settings()

PRODUCTION_INDICATORS = ("prod", "production", "live", "main")


def looks_like_production() -> bool:
    """Refuse to run against obviously-prod environments without --force-prod."""
    env_file_name = os.environ.get("ENV_FILE", "").lower()
    environment = os.environ.get("ENVIRONMENT", "").lower()
    db_url = (os.environ.get("DATABASE_URL") or "").lower()
    for hay in (env_file_name, environment, db_url):
        if any(ind in hay for ind in PRODUCTION_INDICATORS):
            return True
    return False


async def lookup_supabase_user_id(email: str) -> str | None:
    """Find a Supabase Auth user by email. Returns auth_id (uuid str) or None.

    Uses the admin list endpoint with a filter; Supabase paginates but the
    filter narrows to a single row for any real email, so we don't paginate.
    """
    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
    }
    url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params={"email": email})
        if resp.status_code != 200:
            print(
                f"  ⚠️  Supabase list-users failed: {resp.status_code} {resp.text[:200]}"
            )
            return None
        users = resp.json().get("users", [])
        # The `email` filter is a substring/prefix match in some Supabase
        # versions, so insist on an exact case-insensitive match.
        target = email.lower()
        for u in users:
            if (u.get("email") or "").lower() == target:
                return u.get("id")
        return None


async def delete_supabase_user(auth_id: str) -> bool:
    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
    }
    url = f"{settings.SUPABASE_URL}/auth/v1/admin/users/{auth_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers=headers)
        if resp.status_code in (200, 204):
            return True
        print(f"  ✗ Supabase delete-user failed: {resp.status_code} {resp.text[:200]}")
        return False


async def delete_db_rows(email: str) -> dict[str, int]:
    """Delete the app-DB rows tied to `email`. Returns row counts per table."""
    counts: dict[str, int] = {"pending_registrations": 0, "members": 0}
    target = email.lower()

    async with AsyncSessionLocal() as session:
        # pending_registrations — keyed by email
        res = await session.execute(
            text("DELETE FROM pending_registrations " "WHERE LOWER(email) = :email"),
            {"email": target},
        )
        counts["pending_registrations"] = res.rowcount or 0

        # members — keyed by email; CASCADE handles the child tables
        # (member_profiles, member_emergency_contacts, member_availabilities,
        #  member_memberships, member_preferences, coach_profiles).
        res = await session.execute(
            text("DELETE FROM members WHERE LOWER(email) = :email"),
            {"email": target},
        )
        counts["members"] = res.rowcount or 0

        await session.commit()
    return counts


async def run(email: str, skip_confirm: bool) -> int:
    print(f"\nClearing member record for: {email}")
    print(f"Env file:       {env_file}")
    print(f"Supabase URL:   {settings.SUPABASE_URL}")
    print(f"Database URL:   {(os.environ.get('DATABASE_URL') or '')[:60]}...")

    if not skip_confirm:
        print("\nThis will delete the member's DB rows AND their Supabase auth user.")
        try:
            ans = input("Type 'yes' to proceed: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1
        if ans != "yes":
            print("Aborted.")
            return 1

    # 1. Resolve auth_id (so we can delete the Supabase row at the end).
    print("\n[1/3] Looking up Supabase auth user...")
    auth_id = await lookup_supabase_user_id(email)
    if auth_id:
        print(f"  ✓ Found auth_id: {auth_id}")
    else:
        print("  · No Supabase auth user found (already gone or never created).")

    # 2. Delete app DB rows.
    print("\n[2/3] Deleting app DB rows...")
    try:
        counts = await delete_db_rows(email)
    except Exception as e:
        print(f"  ✗ DB deletion failed: {e}")
        return 2
    print(
        f"  ✓ pending_registrations: {counts['pending_registrations']} row(s) deleted"
    )
    print(f"  ✓ members:              {counts['members']} row(s) deleted (cascade)")

    # 3. Delete Supabase auth user.
    print("\n[3/3] Deleting Supabase auth user...")
    if auth_id:
        ok = await delete_supabase_user(auth_id)
        if ok:
            print("  ✓ Supabase auth user deleted")
        else:
            print("  ✗ Supabase auth user delete failed (see error above)")
            return 3
    else:
        print("  · Skipped (no auth user to delete).")

    print(f"\n✅ Done. You can now re-register with {email}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wipe a single member from Supabase Auth + the app DB so "
        "you can re-register with the same email."
    )
    parser.add_argument("email", help="Email address of the member to wipe.")
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    parser.add_argument(
        "--force-prod",
        action="store_true",
        help="Allow running against env files containing 'prod'/'production'/"
        "'live'/'main'. Still requires confirmation unless --yes is also set.",
    )
    args = parser.parse_args()

    if looks_like_production() and not args.force_prod:
        print(
            "❌ Refusing to run: the loaded env looks like production "
            f"(ENV_FILE={env_file!r}). Re-run with --force-prod if you really "
            "mean it."
        )
        return 1

    return asyncio.run(run(args.email, skip_confirm=args.yes))


if __name__ == "__main__":
    sys.exit(main())
