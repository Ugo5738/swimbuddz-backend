"""rls_policies_and_maps_to_index

Hand-written migration — raw DDL (RLS policies + a functional index) that
Alembic autogenerate cannot produce. Generated as a stub via
`./scripts/db/migrate.sh --manual ledger_service` (alembic-assigned revision id
+ correct down_revision); only the upgrade()/downgrade() bodies are authored.

Adds, on top of the initial ledger schema:
  1. A functional index on chart_of_accounts((account_metadata->>'maps_to')) so
     emitters resolve accounts by their stable `maps_to` ref efficiently.
  2. Row-level security on every ledger table, isolating rows by organization.
     ENABLE + FORCE so the policy applies even when the app connects as the
     table owner. Tenant tables key on `org_id`; the tenant root
     (ledger_organizations) keys on its own `id`.

Isolation predicate uses CAST(current_setting('app.current_org_id', true) AS uuid)
— NOT `::uuid`, which SQLAlchemy's text parser misreads as a `:uuid` bind param.
The per-request value is set via `SET LOCAL app.current_org_id` in
app/deps.py:get_ledger_db. When unset, current_setting(..., true) returns NULL,
so the predicate is NULL (no rows / insert blocked) — deny by default.

NOTE: RLS is defence-in-depth. Application-level org_id filtering is still
mandatory on every query. If the DB connection role is a superuser or has
BYPASSRLS, RLS is not enforced regardless — the cross-org isolation test (P1.3)
is the gate that tells us whether it's effective here.

Revision ID: 298d02a91299
Revises: f68ddbb5743d
Create Date: 2026-06-01 11:23:50.182717
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "298d02a91299"
down_revision = "f68ddbb5743d"
branch_labels = None
depends_on = None

# Tenant tables isolated by an org_id column.
ORG_KEYED_TABLES = [
    "chart_of_accounts",
    "cost_centers",
    "ledger_periods",
    "ledger_users",
    "ledger_audit_log",
    "journal_entries",
    "journal_lines",
    "account_balances",
]

_PREDICATE = "CAST(current_setting('app.current_org_id', true) AS uuid)"


def upgrade() -> None:
    # 1. Functional index for resolving accounts by stable maps_to ref.
    op.execute(
        "CREATE INDEX ix_chart_of_accounts_maps_to "
        "ON chart_of_accounts ((account_metadata ->> 'maps_to')) "
        "WHERE (account_metadata ->> 'maps_to') IS NOT NULL"
    )

    # 2. RLS on org-keyed tables.
    for table in ORG_KEYED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_PREDICATE}) "
            f"WITH CHECK (org_id = {_PREDICATE})"
        )

    # 3. RLS on the tenant root (keyed on its own id).
    op.execute("ALTER TABLE ledger_organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ledger_organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY ledger_organizations_org_isolation ON ledger_organizations "
        f"USING (id = {_PREDICATE}) "
        f"WITH CHECK (id = {_PREDICATE})"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS ledger_organizations_org_isolation "
        "ON ledger_organizations"
    )
    op.execute("ALTER TABLE ledger_organizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ledger_organizations DISABLE ROW LEVEL SECURITY")

    for table in ORG_KEYED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP INDEX IF EXISTS ix_chart_of_accounts_maps_to")
