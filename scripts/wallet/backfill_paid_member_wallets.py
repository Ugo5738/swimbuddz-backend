"""Backfill wallets + welcome bonus for already-paid members and coaches.

This script is idempotent:
- Wallet creation is idempotent by member_auth_id.
- Welcome bonus grant is idempotent by key: welcome-bonus-{member_auth_id}.

Usage examples:
  # Preview only (default dry-run): paid members + coaches
  ENV_FILE=.env.prod python scripts/wallet/backfill_paid_member_wallets.py

  # Apply changes
  ENV_FILE=.env.prod python scripts/wallet/backfill_paid_member_wallets.py --apply

  # Restrict to paid members only (exclude unpaid coaches)
  ENV_FILE=.env.prod python scripts/wallet/backfill_paid_member_wallets.py --apply --paid-only

  # Restrict to active paid members only
  ENV_FILE=.env.prod python scripts/wallet/backfill_paid_member_wallets.py --apply --active-only
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import or_, select


def _load_env_file() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env_file = os.environ.get("ENV_FILE", ".env.prod")
    env_path = (project_root / env_file).resolve()
    if not env_path.exists():
        # In containers, env vars are often injected without mounting the env file.
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
    member_id: str
    member_auth_id: str
    email: str
    approval_status: str
    is_active: bool
    roles: list[str]
    has_wallet: bool
    eligible_for_bonus: bool


def _bonus_eligibility(roles: list[str], include_coaches: bool) -> bool:
    normalized = {r.strip().lower() for r in roles if isinstance(r, str) and r.strip()}
    is_coach = "coach" in normalized
    has_member_or_coach_role = "member" in normalized or "coach" in normalized
    return has_member_or_coach_role and (include_coaches or not is_coach)


async def _load_targets(active_only: bool, include_coaches: bool) -> list[TargetMember]:
    from libs.common.config import get_settings
    from libs.db.config import AsyncSessionLocal
    from services.members_service.models import Member, MemberMembership
    from services.wallet_service.models import Wallet

    settings = get_settings()

    async with AsyncSessionLocal() as session:
        query = (
            select(Member, Wallet.id)
            .outerjoin(MemberMembership, MemberMembership.member_id == Member.id)
            .outerjoin(Wallet, Wallet.member_auth_id == Member.auth_id)
            .where(Member.auth_id.is_not(None))
        )
        paid_filter = MemberMembership.community_paid_until.is_not(None)
        if include_coaches:
            query = query.where(or_(paid_filter, Member.roles.any("coach")))
        else:
            query = query.where(paid_filter)
        if active_only:
            query = query.where(Member.is_active.is_(True))

        rows = (await session.execute(query)).all()

    targets: list[TargetMember] = []
    for member, wallet_id in rows:
        roles = list(member.roles or [])
        targets.append(
            TargetMember(
                member_id=str(member.id),
                member_auth_id=member.auth_id,
                email=member.email,
                approval_status=member.approval_status,
                is_active=bool(member.is_active),
                roles=roles,
                has_wallet=wallet_id is not None,
                eligible_for_bonus=_bonus_eligibility(
                    roles=roles,
                    include_coaches=settings.WELCOME_BONUS_INCLUDE_COACHES,
                ),
            )
        )

    return targets


async def _apply(targets: list[TargetMember], limit: int | None) -> None:
    from libs.common.config import get_settings
    from libs.common.service_client import internal_post

    settings = get_settings()
    processed = 0
    failures = 0
    bonus_granted = 0
    wallet_created_or_existing = 0

    for t in targets:
        if limit is not None and processed >= limit:
            break

        response = await internal_post(
            service_url=settings.WALLET_SERVICE_URL,
            path="/internal/wallet/welcome-bonus",
            calling_service="members",
            json={
                "member_id": t.member_id,
                "member_auth_id": t.member_auth_id,
                "eligible": t.eligible_for_bonus,
                "reason": "One-time backfill for paid members and coaches",
                "granted_by": "members_service",
            },
            timeout=20.0,
        )

        processed += 1
        if response.status_code >= 400:
            failures += 1
            print(
                f"[FAIL] auth_id={t.member_auth_id} email={t.email} "
                f"http={response.status_code} body={response.text}"
            )
            continue

        wallet_created_or_existing += 1
        payload = response.json()
        if bool(payload.get("bonus_granted")):
            bonus_granted += 1

    print("")
    print("Backfill complete")
    print(f"Processed: {processed}")
    print(f"Wallet ensured: {wallet_created_or_existing}")
    print(f"Bonus granted now: {bonus_granted}")
    print(f"Failures: {failures}")


def _print_plan(targets: list[TargetMember], limit: int | None) -> None:
    considered = targets if limit is None else targets[:limit]
    total = len(considered)
    missing_wallet = sum(1 for t in considered if not t.has_wallet)
    eligible_for_bonus = sum(1 for t in considered if t.eligible_for_bonus)

    print("Dry run summary")
    print(f"Candidates: {total}")
    print(f"Missing wallets: {missing_wallet}")
    print(f"Eligible for bonus: {eligible_for_bonus}")
    print("")
    print("Sample (first 20):")
    for t in considered[:20]:
        print(
            f"- auth_id={t.member_auth_id} email={t.email} "
            f"approved={t.approval_status} active={t.is_active} "
            f"has_wallet={t.has_wallet} eligible_for_bonus={t.eligible_for_bonus}"
        )


async def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill wallets/welcome bonus for paid members and coaches."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run).",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only include members with is_active=true.",
    )
    parser.add_argument(
        "--paid-only",
        action="store_true",
        help="Only include paid members (exclude unpaid coaches).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of members processed.",
    )
    args = parser.parse_args()

    _load_env_file()

    targets = await _load_targets(
        active_only=args.active_only,
        include_coaches=not args.paid_only,
    )
    _print_plan(targets, limit=args.limit)

    if not args.apply:
        print("")
        print("Dry-run only. Re-run with --apply to execute.")
        return

    await _apply(targets=targets, limit=args.limit)


if __name__ == "__main__":
    asyncio.run(_main())
