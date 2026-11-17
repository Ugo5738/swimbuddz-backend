"""Treasury evidence, requests and spending controls.

Revision ID: feab17112601
Revises: daf6485f9976
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "feab17112601"
down_revision = "daf6485f9976"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE ledger_role_enum ADD VALUE IF NOT EXISTS 'operations'")
    op.create_table(
        "treasury_bank_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("bank_name", sa.String(120), nullable=False),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("last_four", sa.String(4)),
        sa.Column("ledger_account_id", UUID(as_uuid=True), sa.ForeignKey("chart_of_accounts.id")),
        sa.Column("statement_balance_minor", sa.BigInteger()),
        sa.Column("statement_as_of", sa.Date()),
        sa.Column("statement_verified_at", sa.DateTime(timezone=True)),
        sa.Column("statement_evidence", sa.String(250)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "code", name="uq_treasury_bank_code"),
        sa.CheckConstraint("purpose IN ('treasury','operations')", name="ck_treasury_bank_purpose"),
    )
    op.create_index("ix_treasury_bank_accounts_org_id", "treasury_bank_accounts", ["org_id"])
    op.create_table(
        "treasury_statement_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("bank_account_id", UUID(as_uuid=True), sa.ForeignKey("treasury_bank_accounts.id"), nullable=False),
        sa.Column("statement_id", sa.String(160), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(180)),
        sa.Column("match_status", sa.String(30), nullable=False),
        sa.Column("journal_entry_id", UUID(as_uuid=True), sa.ForeignKey("journal_entries.id")),
        sa.Column("request_id", UUID(as_uuid=True)),
        sa.Column("imported_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "bank_account_id", "statement_id", "row_number", name="uq_treasury_statement_row"),
        sa.CheckConstraint("amount_minor <> 0", name="ck_treasury_line_nonzero"),
        sa.CheckConstraint("row_number > 0", name="ck_treasury_row_positive"),
    )
    op.create_index("ix_treasury_statement_lines_org_id", "treasury_statement_lines", ["org_id"])
    op.create_index("ix_treasury_statement_lines_bank_account_id", "treasury_statement_lines", ["bank_account_id"])
    op.create_index("ix_treasury_line_org_date", "treasury_statement_lines", ["org_id", "occurred_on"])
    op.create_table(
        "treasury_budgets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_treasury_budget_positive"),
        sa.CheckConstraint("kind IN ('expense','funding')", name="ck_treasury_budget_kind"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_treasury_budget_dates"),
    )
    op.create_index("ix_treasury_budgets_org_id", "treasury_budgets", ["org_id"])
    op.create_table(
        "treasury_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("source_bank_id", UUID(as_uuid=True), sa.ForeignKey("treasury_bank_accounts.id"), nullable=False),
        sa.Column("destination_bank_id", UUID(as_uuid=True), sa.ForeignKey("treasury_bank_accounts.id")),
        sa.Column("budget_id", UUID(as_uuid=True), sa.ForeignKey("treasury_budgets.id")),
        sa.Column("evidence_reference", sa.String(250)),
        sa.Column("invoice_reference", sa.String(120)),
        sa.Column("vendor_name", sa.String(180)),
        sa.Column("programme", sa.String(80)),
        sa.Column("cost_center_code", sa.String(80)),
        sa.Column("service_on", sa.Date()),
        sa.Column("journal_entry_id", UUID(as_uuid=True), sa.ForeignKey("journal_entries.id")),
        sa.Column("ledger_posted_at", sa.DateTime(timezone=True)),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("approved_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_reason", sa.Text()),
        sa.Column("approval_note", sa.Text()),
        sa.Column("bank_line_id", UUID(as_uuid=True), sa.ForeignKey("treasury_statement_lines.id")),
        sa.Column("destination_bank_line_id", UUID(as_uuid=True), sa.ForeignKey("treasury_statement_lines.id")),
        sa.Column("confirmed_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('expense','funding')", name="ck_treasury_request_kind"),
        sa.CheckConstraint("status IN ('requested','approved','rejected','confirmed')", name="ck_treasury_request_status"),
        sa.CheckConstraint("amount_minor > 0", name="ck_treasury_request_positive"),
    )
    op.create_index("ix_treasury_requests_org_id", "treasury_requests", ["org_id"])
    op.create_index("ix_treasury_requests_org_status", "treasury_requests", ["org_id", "status"])
    op.create_table(
        "treasury_audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("subject_id", sa.String(80), nullable=False),
        sa.Column("details", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_treasury_audit_events_org_id", "treasury_audit_events", ["org_id"])
    op.create_table(
        "treasury_bank_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("bank_account_id", UUID(as_uuid=True), sa.ForeignKey("treasury_bank_accounts.id"), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("balance_minor", sa.BigInteger(), nullable=False),
        sa.Column("evidence_reference", sa.String(250), nullable=False),
        sa.Column("attested_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "bank_account_id", "as_of", name="uq_treasury_bank_snapshot_day"),
    )
    op.create_index("ix_treasury_bank_snapshots_org_id", "treasury_bank_snapshots", ["org_id"])
    op.create_table(
        "treasury_forecast_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("ledger_organizations.id"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("scenario", sa.String(20), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(400), nullable=False),
        sa.Column("programme", sa.String(80)),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("ledger_users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("direction IN ('inflow','outflow')", name="ck_treasury_forecast_direction"),
        sa.CheckConstraint("scenario IN ('base','optimistic','conservative')", name="ck_treasury_forecast_scenario"),
        sa.CheckConstraint("amount_minor > 0", name="ck_treasury_forecast_positive"),
    )
    op.create_index("ix_treasury_forecast_lines_org_id", "treasury_forecast_lines", ["org_id"])
    for table in ("treasury_bank_accounts", "treasury_statement_lines", "treasury_budgets", "treasury_requests", "treasury_audit_events", "treasury_bank_snapshots", "treasury_forecast_lines"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_org_isolation ON {table} "
            "USING (org_id = CAST(current_setting('app.current_org_id', true) AS uuid)) "
            "WITH CHECK (org_id = CAST(current_setting('app.current_org_id', true) AS uuid))"
        )


def downgrade():
    # PostgreSQL enum values cannot be safely removed while preserving existing users.
    for table in ("treasury_forecast_lines", "treasury_bank_snapshots", "treasury_audit_events", "treasury_requests", "treasury_budgets", "treasury_statement_lines", "treasury_bank_accounts"):
        op.drop_table(table)
