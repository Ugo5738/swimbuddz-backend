import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path to import libs
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load env file selected for the reset (defaults to .env.prod when ENV_FILE not set)
project_root = Path(__file__).resolve().parents[2]
env_file = os.environ.get("ENV_FILE", ".env.prod")
load_dotenv(project_root / env_file, override=True)

import httpx
from libs.common.config import get_settings

settings = get_settings()


async def clear_all_supabase_users():
    """Delete all users from Supabase Auth."""
    print("🧹 Clearing all Supabase Auth users...")
    print(f"Connecting to Supabase at {settings.SUPABASE_URL}...")

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # List all users
            list_url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
            print("Fetching all users...")
            response = await client.get(list_url, headers=headers)

            if response.status_code != 200:
                print(
                    f"❌ Failed to list users: {response.status_code} {response.text}"
                )
                return

            data = response.json()
            users = data.get("users", [])

            if not users:
                print("✅ No users found in Supabase Auth")
                return

            print(f"Found {len(users)} user(s) to delete")

            # Delete each user
            deleted_count = 0
            for user in users:
                user_id = user.get("id")
                user_email = user.get("email", "unknown")

                delete_url = f"{settings.SUPABASE_URL}/auth/v1/admin/users/{user_id}"
                delete_response = await client.delete(delete_url, headers=headers)

                if delete_response.status_code in [200, 204]:
                    print(f"  ✓ Deleted user: {user_email}")
                    deleted_count += 1
                else:
                    print(
                        f"  ✗ Failed to delete {user_email}: {delete_response.status_code}"
                    )

            print(f"✅ Deleted {deleted_count}/{len(users)} users from Supabase Auth")

        except Exception as e:
            print(f"❌ Exception during Supabase user cleanup: {e}")
            return


if __name__ == "__main__":
    asyncio.run(clear_all_supabase_users())
