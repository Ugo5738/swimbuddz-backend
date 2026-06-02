"""rls_reconciliation_and_recognition_tables

Hand-written migration — raw DDL (RLS policies) that Alembic autogenerate cannot
produce. Generated as a stub via `./scripts/db/migrate.sh --manual ledger_service`
(alembic-assigned revision id + correct down_revision); only the
upgrade()/downgrade() bodies are authored.

Brings three org-keyed tables under the same org-isolation RLS as the rest of the
ledger (mirrors 298d02a91299): ``revenue_recognition_schedules`` (added in R1
without RLS), and the R3-PR2 reconciliation tables ``external_transactions`` and
``reconciliation_breaks``. ENABLE + FORCE so the policy applies even when the app
connects as the table owner; the per-request org is set via
``SET LOCAL app.current_org_id`` in app/deps.py:get_ledger_db. RLS is
defence-in-depth — application-level org_id filtering remains mandatory.

Revision ID: cf2eae9376e5
Revises: de1babbc521e
Create Date: 2026-06-02 11:30:40.609372
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cf2eae9376e5"
down_revision = "de1babbc521e"
branch_labels = None
depends_on = None

ORG_KEYED_TABLES = [
    "revenue_recognition_schedules",
    "external_transactions",
    "reconciliation_breaks",
]

_PREDICATE = "CAST(current_setting('app.current_org_id', true) AS uuid)"


def upgrade() -> None:
    for table in ORG_KEYED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            f"USING (org_id = {_PREDICATE}) "
            f"WITH CHECK (org_id = {_PREDICATE})"
        )


def downgrade() -> None:
    for table in ORG_KEYED_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
