"""Add immutable evidence-backed Academy competency passport records.

Revision ID: a20261123passport
Revises: a20261122req

No legacy grades, enrollments, rewards or certificates are backfilled.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261123passport"
down_revision = "a20261122req"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academy_competency_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_auth_id", sa.String(160), nullable=False),
        sa.Column(
            "source_enrollment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source_progress_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_review_event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "milestone_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_milestone_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "milestone_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_milestone_versions.id"),
            nullable=False,
        ),
        sa.Column("rubric_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("criteria_passed", postgresql.JSONB(), nullable=False),
        sa.Column("assessment_context_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_media_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_owner_auth_id", sa.String(160), nullable=False),
        sa.Column("verified_by_auth_id", sa.String(160), nullable=False),
        sa.Column("review_notes", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source_review_event_id", name="uq_acv_source_review"
        ),
    )
    op.create_index(
        "ix_acv_member_auth_milestone",
        "academy_competency_verifications",
        ["member_auth_id", "milestone_definition_id"],
    )
    op.create_index(
        "ix_academy_competency_verifications_member_auth_id",
        "academy_competency_verifications",
        ["member_auth_id"],
    )
    op.create_index(
        "ix_academy_competency_verifications_source_progress_id",
        "academy_competency_verifications",
        ["source_progress_id"],
    )
    op.create_table(
        "academy_competency_revocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_competency_verifications.id"),
            nullable=False,
        ),
        sa.Column("revoked_by_auth_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("verification_id", name="uq_acr_verification"),
    )
    op.create_index(
        "ix_academy_competency_revocations_verification_id",
        "academy_competency_revocations",
        ["verification_id"],
    )
    # Keep the ledger append-only even if a future API adds an ORM edit method.
    op.execute(
        """
        CREATE FUNCTION guard_academy_competency_ledger() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Academy competency ledger is append-only'
                USING ERRCODE = 'integrity_constraint_violation';
        END;
        $$;
        """
    )
    for table in (
        "academy_competency_verifications",
        "academy_competency_revocations",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_academy_competency_ledger()"
        )


def downgrade() -> None:
    for table in (
        "academy_competency_revocations",
        "academy_competency_verifications",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION guard_academy_competency_ledger()")
    op.drop_index(
        "ix_academy_competency_revocations_verification_id",
        table_name="academy_competency_revocations",
    )
    op.drop_table("academy_competency_revocations")
    op.drop_index(
        "ix_acv_member_auth_milestone",
        table_name="academy_competency_verifications",
    )
    op.drop_index(
        "ix_academy_competency_verifications_member_auth_id",
        table_name="academy_competency_verifications",
    )
    op.drop_index(
        "ix_academy_competency_verifications_source_progress_id",
        table_name="academy_competency_verifications",
    )
    op.drop_table("academy_competency_verifications")
