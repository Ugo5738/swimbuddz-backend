import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from dotenv import load_dotenv
from services.members_service.models import (
    Member,
    MemberAvailability,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload


def load_env_file() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env_file = os.environ.get("ENV_FILE", ".env.prod")
    env_path = (project_root / env_file).resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"Env file not found: {env_path}")
    load_dotenv(env_path, override=True)


load_env_file()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")


def database_url() -> str:
    for key in ("DATABASE_SESSION_URL", "DATABASE_TRANSACTION_URL", "DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            return value
    raise RuntimeError("No database URL found.")


def parse_admin_emails() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return {str(v).lower() for v in data if v}
    except Exception:
        pass
    # Fallback: comma-separated
    return {v.strip().lower() for v in raw.split(",") if v.strip()}


ADMIN_EMAILS = parse_admin_emails()

VALID_TIERS = {"community", "club", "academy"}


def normalize_roles(raw_roles: Iterable[Any], email: str | None) -> list[str]:
    normalized: list[str] = []
    for role in raw_roles:
        if not isinstance(role, str):
            continue
        value = role.strip().lower()
        if value and value not in normalized:
            normalized.append(value)

    if email and email.lower() in ADMIN_EMAILS and "admin" not in normalized:
        normalized.append("admin")

    if not normalized:
        normalized = ["member"]
    return normalized


def derive_name(email: str | None, user_meta: dict[str, Any]) -> tuple[str, str]:
    first_name = (
        user_meta.get("first_name")
        or user_meta.get("firstName")
        or user_meta.get("given_name")
    )
    last_name = (
        user_meta.get("last_name")
        or user_meta.get("lastName")
        or user_meta.get("family_name")
    )
    full_name = user_meta.get("full_name") or user_meta.get("name")

    if (not first_name or not last_name) and full_name:
        parts = [p for p in str(full_name).replace(",", " ").split() if p]
        if parts:
            first_name = first_name or parts[0]
            if len(parts) > 1:
                last_name = last_name or parts[-1]

    if (not first_name or not last_name) and email:
        local = email.split("@")[0]
        tokens = [t for t in local.replace("-", ".").replace("_", ".").split(".") if t]
        if tokens:
            first_name = first_name or tokens[0].capitalize()
            if len(tokens) > 1:
                last_name = last_name or tokens[1].capitalize()

    if not first_name:
        first_name = "Member"
    if not last_name:
        last_name = "User"
    return str(first_name), str(last_name)


def parse_requested_tiers(user_meta: dict[str, Any]) -> list[str]:
    raw = user_meta.get("requested_membership_tiers")
    if isinstance(raw, list):
        tiers = [str(t).lower() for t in raw if t]
    elif isinstance(raw, str):
        tiers = [raw.lower()]
    else:
        tiers = []
    return [t for t in tiers if t in VALID_TIERS]


PAID_COMMUNITY_OVERRIDES: dict[str, datetime] = {
    "usihpeter@gmail.com": datetime(2027, 2, 3, tzinfo=timezone.utc),
}

USER_OVERRIDES: dict[str, dict[str, Any]] = {
    # Godspower completed onboarding for community; keep intent as community.
    "godspowerakunne02@gmail.com": {
        "requested_tiers": [],
        "roles": ["member"],
    },
    # Joseph registered to apply as a coach.
    "emuezejoseph@gmail.com": {
        "roles": ["coach", "member"],
    },
}


async def fetch_supabase_users() -> list[dict[str, Any]]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    users: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            params = {"page": page, "per_page": per_page}
            url = f"{SUPABASE_URL}/auth/v1/admin/users"
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("users", [])
            if not batch:
                break
            users.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
    return users


async def ensure_sub_records(session: AsyncSession, member_id) -> None:
    profile_id = await session.scalar(
        select(MemberProfile.id).where(MemberProfile.member_id == member_id)
    )
    if not profile_id:
        session.add(MemberProfile(member_id=member_id))

    emergency_id = await session.scalar(
        select(MemberEmergencyContact.id).where(
            MemberEmergencyContact.member_id == member_id
        )
    )
    if not emergency_id:
        session.add(MemberEmergencyContact(member_id=member_id))

    availability_id = await session.scalar(
        select(MemberAvailability.id).where(MemberAvailability.member_id == member_id)
    )
    if not availability_id:
        session.add(MemberAvailability(member_id=member_id))

    membership_id = await session.scalar(
        select(MemberMembership.id).where(MemberMembership.member_id == member_id)
    )
    if not membership_id:
        session.add(
            MemberMembership(
                member_id=member_id,
                primary_tier="community",
                active_tiers=["community"],
                requested_tiers=[],
            )
        )

    preferences_id = await session.scalar(
        select(MemberPreferences.id).where(MemberPreferences.member_id == member_id)
    )
    if not preferences_id:
        session.add(MemberPreferences(member_id=member_id))


async def upsert_member(session: AsyncSession, user: dict[str, Any]) -> None:
    auth_id = user.get("id")
    email = user.get("email")
    if not auth_id or not email:
        return

    user_meta = user.get("user_metadata") or {}
    app_meta = user.get("app_metadata") or {}

    first_name, last_name = derive_name(email, user_meta)

    raw_roles: list[Any] = []
    if isinstance(app_meta.get("roles"), list):
        raw_roles.extend(app_meta.get("roles"))
    elif isinstance(app_meta.get("roles"), str):
        raw_roles.append(app_meta.get("roles"))
    if isinstance(user_meta.get("roles"), list):
        raw_roles.extend(user_meta.get("roles"))
    elif isinstance(user_meta.get("roles"), str):
        raw_roles.append(user_meta.get("roles"))

    roles = normalize_roles(raw_roles, email)
    requested_tiers = parse_requested_tiers(user_meta)

    overrides = USER_OVERRIDES.get(email.lower(), {})
    if overrides.get("roles"):
        roles = overrides["roles"]
    if overrides.get("requested_tiers") is not None:
        requested_tiers = overrides["requested_tiers"]

    community_paid_until = PAID_COMMUNITY_OVERRIDES.get(email.lower())
    registration_complete = community_paid_until is not None
    if "registration_complete" in overrides:
        registration_complete = bool(overrides["registration_complete"])

    member = await session.scalar(
        select(Member)
        .options(
            selectinload(Member.profile),
            selectinload(Member.emergency_contact),
            selectinload(Member.availability),
            selectinload(Member.membership),
            selectinload(Member.preferences),
        )
        .where(Member.auth_id == auth_id)
    )

    if not member:
        member = Member(
            auth_id=auth_id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            registration_complete=registration_complete,
            approval_status="approved",
            roles=roles,
        )
        session.add(member)
        await session.flush()
    else:
        member.email = email
        member.first_name = first_name
        member.last_name = last_name
        member.roles = roles
        member.registration_complete = registration_complete
        member.approval_status = "approved"

    await ensure_sub_records(session, member.id)

    membership = await session.scalar(
        select(MemberMembership).where(MemberMembership.member_id == member.id)
    )
    if membership:
        membership.primary_tier = "community"
        membership.active_tiers = ["community"]
        membership.requested_tiers = requested_tiers or []
        membership.community_paid_until = community_paid_until

    preferences = await session.scalar(
        select(MemberPreferences).where(MemberPreferences.member_id == member.id)
    )
    if preferences and "community_rules_accepted" in user_meta:
        preferences.community_rules_accepted = bool(
            user_meta.get("community_rules_accepted")
        )

    profile = await session.scalar(
        select(MemberProfile).where(MemberProfile.member_id == member.id)
    )
    if profile:
        if user_meta.get("phone"):
            profile.phone = user_meta.get("phone")
        if user_meta.get("city"):
            profile.city = user_meta.get("city")
        if user_meta.get("state"):
            profile.state = user_meta.get("state")
        if user_meta.get("country"):
            profile.country = user_meta.get("country")

    await session.commit()


async def backfill() -> None:
    users = await fetch_supabase_users()
    print(f"Found {len(users)} user(s) in Supabase Auth.")

    engine = create_async_engine(database_url(), echo=False)
    async with AsyncSession(engine) as session:
        for user in users:
            await upsert_member(session, user)

    await engine.dispose()
    print("Backfill complete.")


if __name__ == "__main__":
    asyncio.run(backfill())
