"""Add frozen published Academy requirements alongside legacy progress.

Revision ID: a20261122req
Revises: a20261122catalog

This migration does not backfill achievements, alter attendance, or change
certificate eligibility. Only new, explicit admin snapshots use this table.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261122req"
down_revision = "a20261122catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academy_cohort_requirements_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cohort_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cohorts.id"),
            nullable=False,
        ),
        sa.Column(
            "program_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("programs.id"),
            nullable=False,
        ),
        sa.Column("requirements_json", postgresql.JSONB(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_id", name="uq_acrs_cohort"),
    )
    op.create_index(
        "ix_academy_cohort_requirements_snapshots_cohort_id",
        "academy_cohort_requirements_snapshots",
        ["cohort_id"],
    )
    # Even an accidental ORM update/delete must not rewrite a cohort's
    # published learning contract. Later changes require a versioned amendment.
    op.execute(
        """
        CREATE FUNCTION guard_academy_requirements_snapshot() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                'Published Academy requirements snapshots are immutable'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_academy_requirements_immutable
        BEFORE UPDATE OR DELETE ON academy_cohort_requirements_snapshots
        FOR EACH ROW EXECUTE FUNCTION guard_academy_requirements_snapshot();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_academy_requirements_immutable "
        "ON academy_cohort_requirements_snapshots"
    )
    op.execute("DROP FUNCTION guard_academy_requirements_snapshot()")
    op.drop_index(
        "ix_academy_cohort_requirements_snapshots_cohort_id",
        table_name="academy_cohort_requirements_snapshots",
    )
    op.drop_table("academy_cohort_requirements_snapshots")
