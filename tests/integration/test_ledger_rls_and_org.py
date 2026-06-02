"""Ledger hardening tests.

#24 — org resolution falls back to the sole org when LEDGER_DEFAULT_ORG_ID is
unset (single-tenant self-heal) and still prefers the env var when set.

#13 — every org-keyed ledger table has RLS enabled + an org-isolation policy.
This is the safe half of the non-BYPASSRLS work: it guarantees the policies are
*defined* (so the eventual B2B role cutover just works) and fails loudly if a
new table ever ships without RLS — the exact gap that revenue_recognition_schedules
had. (RLS only *enforces* once the app connects as a non-BYPASSRLS role; under
the current `postgres` role it's inert — see scripts/seed/ledger_org.py.)
"""

import uuid

import services.ledger_service.app.deps as deps
from sqlalchemy import text

# Every org-keyed ledger table that must carry an org-isolation RLS policy
# (mirrors migrations 298d02a91299 + cf2eae9376e5). ledger_organizations keys on
# its own id; the rest on org_id.
EXPECTED_RLS_TABLES = {
    "ledger_organizations",
    "chart_of_accounts",
    "cost_centers",
    "ledger_periods",
    "ledger_users",
    "ledger_audit_log",
    "journal_entries",
    "journal_lines",
    "account_balances",
    "revenue_recognition_schedules",
    "external_transactions",
    "reconciliation_breaks",
    "invoices",
    "invoice_lines",
    "invoice_sequences",
}


async def test_resolve_org_prefers_env_when_set(monkeypatch, db_session):
    fixed = uuid.uuid4()
    monkeypatch.setattr(deps, "_env_org_id", lambda: fixed)
    monkeypatch.setattr(deps, "_FALLBACK_ORG_ID", None)
    # Env wins without touching the DB.
    assert await deps._resolve_org_id(db_session) == fixed


async def test_resolve_org_falls_back_to_sole_org_when_env_unset(
    monkeypatch, db_session
):
    monkeypatch.setattr(deps, "_env_org_id", lambda: None)
    monkeypatch.setattr(deps, "_FALLBACK_ORG_ID", None)
    # Single-tenant dev DB has exactly one org -> fallback resolves to it.
    org_id = await deps._resolve_org_id(db_session)
    assert isinstance(org_id, uuid.UUID)
    # Cached for subsequent calls.
    assert deps._FALLBACK_ORG_ID == org_id


async def test_org_keyed_ledger_tables_have_rls(db_session):
    secured = {
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND c.relrowsecurity = true"
                )
            )
        ).all()
    }
    missing = EXPECTED_RLS_TABLES - secured
    assert not missing, f"ledger tables missing RLS ENABLE: {missing}"

    policy_rows = (
        await db_session.execute(
            text(
                "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'"
            )
        )
    ).all()
    policy_tables = {t for (t, p) in policy_rows if p.endswith("_org_isolation")}
    missing_policy = EXPECTED_RLS_TABLES - policy_tables
    assert (
        not missing_policy
    ), f"ledger tables missing org_isolation policy: {missing_policy}"
