"""Backfill volunteer profiles for members who don't have one yet.

This script is idempotent — the internal endpoint returns success if the
profile already exists.

Usage:
  # Preview (dry-run, default):
  ENV_FILE=.env.prod python scripts/volunteer/backfill_volunteer_profiles.py

  # Apply changes:
  ENV_FILE=.env.prod python scripts/volunteer/backfill_volunteer_profiles.py --apply

  # Single member by name:
  ENV_FILE=.env.prod python scripts/volunteer/backfill_volunteer_profiles.py --apply --name "Onyinye Opara"

  # Only members who expressed volunteer interest:
  ENV_FILE=.env.prod python scripts/volunteer/backfill_volunteer_profiles.py --apply --with-interests-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select


def _load_env_file() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env_file = os.environ.get("ENV_FILE", ".env.prod")
    env_path = (project_root / env_file).resolve()
    if not env_path.exists():
        if any(
            os.environ.get(key)
            for key in ("DATABASE_SESSION_URL", "DATABASE_URL", "SUPABASE_JWT_SECRET")
        ):
            print(f"Env file not found at {env_path}; using existing environment vars.")
            return
        raise FileNotFoundError(f"Env file not found: {env_path}")
    load_dotenv(env_path, override=True)


@dataclass
class TargetMember:
    id: str
    first_name: str
    last_name: str
    volunteer_interests: list[str] = field(default_factory=list)


async def _find_members(
    *,
    name_filter: str | None = None,
    with_interests_only: bool = False,
) -> list[TargetMember]:
    """Query members_service DB for members missing volunteer profiles."""
    from libs.db.config import AsyncSessionLocal
    from services.members_service.models import Member, MemberPreferences

    async with AsyncSessionLocal() as db:
        q = (
            select(
                Member.id,
                Member.first_name,
                Member.last_name,
                MemberPreferences.volunteer_interest,
            )
            .outerjoin(MemberPreferences, MemberPreferences.member_id == Member.id)
            .where(Member.registration_complete.is_(True))
        )

        if name_filter:
            parts = name_filter.strip().split()
            if len(parts) >= 2:
                from sqlalchemy import func

                q = q.where(
                    func.lower(Member.first_name).contains(parts[0].lower()),
                    func.lower(Member.last_name).contains(parts[-1].lower()),
                )
            else:
                from sqlalchemy import func

                q = q.where(
                    func.lower(Member.first_name).contains(name_filter.lower())
                    | func.lower(Member.last_name).contains(name_filter.lower())
                )

        if with_interests_only:
            from sqlalchemy import func

            q = q.where(
                MemberPreferences.volunteer_interest.isnot(None),
                func.array_length(MemberPreferences.volunteer_interest, 1) > 0,
            )

        rows = (await db.execute(q)).all()

    return [
        TargetMember(
            id=str(row[0]),
            first_name=row[1] or "",
            last_name=row[2] or "",
            volunteer_interests=row[3] or [],
        )
        for row in rows
    ]


async def _ensure_profile(member: TargetMember, *, apply: bool) -> str:
    """Call volunteer_service internal endpoint to create profile."""
    from libs.common.config import get_settings
    from libs.common.service_client import internal_post

    settings = get_settings()
    name = f"{member.first_name} {member.last_name}".strip()

    if not apply:
        interests_str = (
            ", ".join(member.volunteer_interests)
            if member.volunteer_interests
            else "(none)"
        )
        return f"[DRY-RUN] Would create profile for {name} (interests: {interests_str})"

    payload: dict = {"member_id": member.id}
    if member.volunteer_interests:
        payload["volunteer_interests"] = member.volunteer_interests

    try:
        resp = await internal_post(
            service_url=settings.VOLUNTEER_SERVICE_URL,
            path="/internal/volunteer/ensure-profile",
            calling_service="backfill-script",
            json=payload,
            timeout=15.0,
        )
        if resp.status_code < 400:
            data = resp.json()
            created = data.get("created", False)
            action = "Created" if created else "Already exists"
            return f"[OK] {action} profile for {name}"
        else:
            return f"[FAIL] HTTP {resp.status_code} for {name}: {resp.text}"
    except Exception as exc:
        return f"[FAIL] {name}: {exc}"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill volunteer profiles")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create profiles (default is dry-run)",
    )
    parser.add_argument("--name", type=str, default=None, help="Filter by member name")
    parser.add_argument(
        "--with-interests-only",
        action="store_true",
        help="Only members who expressed volunteer interest",
    )
    args = parser.parse_args()

    _load_env_file()

    print("Finding members...")
    members = await _find_members(
        name_filter=args.name,
        with_interests_only=args.with_interests_only,
    )

    if not members:
        print("No matching members found.")
        return

    print(f"Found {len(members)} member(s).\n")

    for member in members:
        result = await _ensure_profile(member, apply=args.apply)
        print(result)

    if not args.apply:
        print(f"\nDry-run complete. Use --apply to create {len(members)} profile(s).")


if __name__ == "__main__":
    asyncio.run(main())
