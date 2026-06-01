"""Chart-of-accounts seeding and account-ref resolution.

`seed_chart_of_accounts` loads a vertical template (coa_templates/*.yaml) and
creates the accounts for an org idempotently. `resolve_account_ids` maps the
stable `maps_to` refs emitters use back to account ids (using the functional
index from migration 298d02a91299).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Iterable

import yaml
from services.ledger_service.models import ChartOfAccounts
from services.ledger_service.models.enums import AccountType, NormalBalance
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "coa_templates"


def load_coa_template(vertical: str = "sports_club") -> list[dict]:
    """Load the account list from coa_templates/<vertical>.yaml."""
    path = _TEMPLATE_DIR / f"{vertical}.yaml"
    with path.open() as fh:
        data = yaml.safe_load(fh)
    accounts = data.get("accounts")
    if not accounts:
        raise ValueError(f"CoA template {path} has no 'accounts'")
    return accounts


async def seed_chart_of_accounts(
    session: AsyncSession, org_id: uuid.UUID, vertical: str = "sports_club"
) -> dict:
    """Idempotently create an org's chart of accounts from a template.

    Inserts only accounts whose code doesn't already exist for the org, then
    resolves parent_id links by code. Returns {created, existing, parent_links}.
    Caller commits.
    """
    template = load_coa_template(vertical)

    existing_codes = set(
        (
            await session.execute(
                select(ChartOfAccounts.code).where(ChartOfAccounts.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )

    created = 0
    for acct in template:
        if acct["code"] in existing_codes:
            continue
        session.add(
            ChartOfAccounts(
                org_id=org_id,
                code=acct["code"],
                name=acct["name"],
                type=AccountType(acct["type"]),
                normal_balance=NormalBalance(acct["normal_balance"]),
                is_active=acct.get("is_active", True),
                is_system=acct.get("is_system", True),
                account_metadata=(
                    {"maps_to": acct["maps_to"]} if acct.get("maps_to") else None
                ),
            )
        )
        created += 1
    await session.flush()

    # Resolve parent_id by code (second pass, now that all rows have ids).
    code_to_id = dict(
        (
            await session.execute(
                select(ChartOfAccounts.code, ChartOfAccounts.id).where(
                    ChartOfAccounts.org_id == org_id
                )
            )
        ).all()
    )
    template_by_code = {a["code"]: a for a in template}
    rows = (
        (
            await session.execute(
                select(ChartOfAccounts).where(ChartOfAccounts.org_id == org_id)
            )
        )
        .scalars()
        .all()
    )
    parent_links = 0
    for row in rows:
        parent_code = template_by_code.get(row.code, {}).get("parent")
        if parent_code and row.parent_id is None:
            parent_id = code_to_id.get(parent_code)
            if parent_id:
                row.parent_id = parent_id
                parent_links += 1
    await session.flush()

    return {
        "created": created,
        "existing": len(existing_codes),
        "parent_links": parent_links,
    }


async def resolve_account_ids(
    session: AsyncSession, org_id: uuid.UUID, refs: Iterable[str]
) -> dict[str, uuid.UUID]:
    """Map stable `maps_to` refs to account ids for an org.

    Raises ValueError listing any refs that don't resolve to an active account —
    surfacing a CoA/emitter mismatch loudly instead of silently mis-posting.
    """
    wanted = list(dict.fromkeys(refs))  # dedupe, preserve order
    if not wanted:
        return {}
    maps_to = ChartOfAccounts.account_metadata["maps_to"].astext
    rows = (
        await session.execute(
            select(maps_to, ChartOfAccounts.id).where(
                ChartOfAccounts.org_id == org_id,
                ChartOfAccounts.is_active.is_(True),
                maps_to.in_(wanted),
            )
        )
    ).all()
    resolved = {ref: acct_id for ref, acct_id in rows}
    missing = [r for r in wanted if r not in resolved]
    if missing:
        raise ValueError(f"unresolved account refs for org {org_id}: {missing}")
    return resolved
