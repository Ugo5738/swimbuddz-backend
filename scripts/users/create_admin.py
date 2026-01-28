import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add parent directory to path to import libs
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load env file selected for the reset (defaults to .env.prod when ENV_FILE not set)
# MUST be done before importing libs that use get_settings()
project_root = Path(__file__).resolve().parents[2]
env_file = os.environ.get("ENV_FILE", ".env.prod")
load_dotenv(project_root / env_file, override=True)

import httpx
from sqlalchemy import select

from libs.common.config import get_settings
from libs.db.config import AsyncSessionLocal
from services.members_service.models import Member

settings = get_settings()


async def create_admin_user():
    print("🚀 Starting Admin User Creation Script")
    print(f"DEBUG: SUPABASE_URL = {settings.SUPABASE_URL}")

    email = "admin@admin.com"
    password = "admin"  # Default password, change immediately
    app_metadata = {"role": "admin", "roles": ["admin", "authenticated"]}
    user_metadata = {"full_name": "Admin User"}

    # 1. Create Supabase Auth User via HTTPX
    print(f"Connecting to Supabase at {settings.SUPABASE_URL}...")

    auth_uid = None

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            # Create User
            url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
            payload = {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": user_metadata,
                "app_metadata": app_metadata,
            }

            print(f"Creating Supabase user {email}...")
            response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 200:
                data = response.json()
                auth_uid = data["id"]  # or data["user"]["id"] depending on API version
                # Usually returns User object directly or wrapped
                if "user" in data:
                    auth_uid = data["user"]["id"]
                print(f"✅ Supabase Auth User Created: {auth_uid}")
            elif (
                response.status_code == 422
                or "already registered" in response.text
                or "already exists" in response.text
            ):
                print("⚠️ User already exists in Supabase. Fetching ID...")
                # Try to list users to find ID (admin only)
                # GET /auth/v1/admin/users
                # This might be paginated, but let's try to find by email if possible or just list
                # Actually, sign-in is easier if we know the password

                # Try sign-in
                login_url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
                login_payload = {"email": email, "password": password}
                # Note: This endpoint usually requires anon key, but service role works too?
                # Let's use anon key for login just in case
                login_headers = {
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Content-Type": "application/json",
                }

                login_res = await client.post(
                    login_url, json=login_payload, headers=login_headers
                )
                if login_res.status_code == 200:
                    login_data = login_res.json()
                    auth_uid = login_data["user"]["id"]
                    print(f"✅ Found existing user ID: {auth_uid}")
                else:
                    print(f"❌ Could not sign in to get ID: {login_res.text}")
                    # Fallback: List users (admin)
                    list_url = f"{settings.SUPABASE_URL}/auth/v1/admin/users"
                    list_res = await client.get(list_url, headers=headers)
                    if list_res.status_code == 200:
                        users = list_res.json().get("users", [])
                        for u in users:
                            if u["email"] == email:
                                auth_uid = u["id"]
                                print(
                                    f"✅ Found existing user ID via Admin List: {auth_uid}"
                                )
                                break
            else:
                print(
                    f"❌ Failed to create Supabase user: {response.status_code} {response.text}"
                )
                return

        except Exception as e:
            print(f"❌ Exception during Supabase request: {e}")
            return

    if not auth_uid:
        print("❌ Could not obtain Auth UID. Aborting.")
        return

    # Ensure admin app_metadata is set so JWT carries the claim
    # Use a fresh client to ensure the connection is open when updating metadata
    try:
        update_url = f"{settings.SUPABASE_URL}/auth/v1/admin/users/{auth_uid}"
        update_payload = {"app_metadata": app_metadata, "user_metadata": user_metadata}
        async with httpx.AsyncClient() as update_client:
            update_res = await update_client.put(
                update_url, json=update_payload, headers=headers
            )
        if update_res.status_code in (200, 201):
            print("✅ Admin app_metadata updated")
        else:
            print(
                f"⚠️ Could not update app_metadata: {update_res.status_code} {update_res.text}"
            )
    except Exception as e:
        print(f"⚠️ Exception updating app_metadata: {e}")

    # 2. Create Member Record in DB
    print("Connecting to database...")
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check if member exists
            result = await session.execute(select(Member).where(Member.email == email))
            existing_member = result.scalars().first()

            if existing_member:
                print(f"⚠️ Member record already exists for {email}.")
                if existing_member.auth_id != auth_uid:
                    print(
                        f"⚠️ Updating auth_id from {existing_member.auth_id} to {auth_uid}"
                    )
                    existing_member.auth_id = auth_uid
                    existing_member.is_active = True
                    existing_member.registration_complete = True
                    print("✅ Member updated.")
                # Ensure admin role is present in roles array
                if (
                    existing_member.roles is None
                    or "admin" not in existing_member.roles
                ):
                    updated_roles = list(existing_member.roles or [])
                    updated_roles.append("admin")
                    existing_member.roles = updated_roles
                    print("✅ Member roles updated with admin.")
            else:
                print("Creating new Member record...")
                new_member = Member(
                    id=uuid4(),
                    auth_id=auth_uid,
                    email=email,
                    first_name="Admin",
                    last_name="User",
                    is_active=True,
                    registration_complete=True,
                    roles=["admin", "member"],
                    approval_status="approved",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(new_member)
                # Note: Admin has no membership tier - admin is a role, not a tier
                print("✅ Member record created.")

    print("\n🎉 Admin setup complete!")
    print(f"Email: {email}")
    print(f"Password: {password}")
    print("You can now log in at http://localhost:3000/login")


if __name__ == "__main__":
    asyncio.run(create_admin_user())
