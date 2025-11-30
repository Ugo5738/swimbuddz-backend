import asyncio
import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path to import libs
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load .env.dev explicitly
load_dotenv(".env.dev", override=True)

import httpx
from libs.common.config import get_settings

settings = get_settings()


async def seed_academy_data():
    print("🚀 Seeding Academy Data...")

    # Use the admin user we created
    # In a real scenario, we'd login to get a token, but for simplicity
    # we'll use the service role key to bypass auth or assume the endpoint allows it
    # (The Academy Service endpoints require auth, so we need a token or service role)
    # The Gateway verifies the token.
    # Let's try to login as admin to get a valid token.

    email = "admin@admin.com"
    password = "admin"

    token = None

    async with httpx.AsyncClient() as client:
        # 1. Login to get token
        print("Logging in...")
        login_url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
        login_payload = {"email": email, "password": password}
        login_headers = {
            "apikey": settings.SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        }

        try:
            res = await client.post(
                login_url, json=login_payload, headers=login_headers
            )
            if res.status_code == 200:
                token = res.json()["access_token"]
                print("✅ Logged in successfully.")
            else:
                print(f"❌ Login failed: {res.text}")
                return
        except Exception as e:
            print(f"❌ Login exception: {e}")
            return

        # 2. Create Program
        print("Creating Program...")
        # Gateway URL (internal docker network)
        base_url = "http://gateway:8000/api/v1/academy"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        program_payload = {
            "name": "Adult Learn to Swim",
            "description": "A comprehensive program for adults learning to swim from scratch.",
            "level": "beginner_1",
            "duration_weeks": 8,
            "curriculum_json": {"week1": "Floating", "week2": "Kicking"},
        }

        program_id = None

        try:
            res = await client.post(
                f"{base_url}/programs", json=program_payload, headers=headers
            )
            if res.status_code == 200:
                data = res.json()
                program_id = data["id"]
                print(f"✅ Program created: {data['name']} ({program_id})")
            else:
                print(f"❌ Create Program failed: {res.status_code} {res.text}")
                # Try to list to see if it exists (idempotency check manual)
                # For now just return
                return
        except Exception as e:
            print(f"❌ Create Program exception: {repr(e)}")
            # Print response text if available
            # print(f"Response: {res.text}")
            return

        # 3. Create Cohort
        print("Creating Cohort...")
        start_date = datetime.utcnow().date()
        end_date = start_date + timedelta(weeks=8)

        cohort_payload = {
            "program_id": program_id,
            "name": "Batch A - Jan 2025",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "capacity": 10,
            "status": "open",
        }

        try:
            res = await client.post(
                f"{base_url}/cohorts", json=cohort_payload, headers=headers
            )
            if res.status_code == 200:
                data = res.json()
                print(f"✅ Cohort created: {data['name']} ({data['id']})")
            else:
                print(f"❌ Create Cohort failed: {res.status_code} {res.text}")
        except Exception as e:
            print(f"❌ Create Cohort exception: {e}")


if __name__ == "__main__":
    asyncio.run(seed_academy_data())
