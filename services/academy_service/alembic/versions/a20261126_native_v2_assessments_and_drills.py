"""Add native V2 assessment provenance and TI remedial/personalized drill tables.

Revision ID: a20261126v2drills
Revises: a20261123passport

- Extends academy_competency_verifications with source_type and assessor_role,
  and allows nullable legacy source_* IDs guarded by ck_acv_source_provenance.
- Preserves guard_academy_competency_ledger append-only immutability trigger.
- Adds academy_milestone_remedial_drills and academy_student_drill_assignments.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261126v2drills"
down_revision = "a20261123passport"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "academy_competency_verifications",
        sa.Column(
            "source_type",
            sa.String(32),
            nullable=False,
            server_default="legacy_promotion",
        ),
    )
    op.add_column(
        "academy_competency_verifications",
        sa.Column(
            "assessor_role",
            sa.String(20),
            nullable=False,
            server_default="admin",
        ),
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_enrollment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_progress_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_review_event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_acv_source_type",
        "academy_competency_verifications",
        "source_type IN ('legacy_promotion','cohort_v2','diagnostic_assessment')",
    )
    op.create_check_constraint(
        "ck_acv_assessor_role",
        "academy_competency_verifications",
        "assessor_role IN ('coach','admin')",
    )
    op.create_check_constraint(
        "ck_acv_source_provenance",
        "academy_competency_verifications",
        "(source_type = 'legacy_promotion' "
        "AND source_enrollment_id IS NOT NULL "
        "AND source_progress_id IS NOT NULL "
        "AND source_review_event_id IS NOT NULL) "
        "OR (source_type = 'cohort_v2' "
        "AND source_enrollment_id IS NOT NULL "
        "AND source_progress_id IS NULL "
        "AND source_review_event_id IS NULL) "
        "OR (source_type = 'diagnostic_assessment' "
        "AND source_enrollment_id IS NULL "
        "AND source_progress_id IS NULL "
        "AND source_review_event_id IS NULL)",
    )

    op.create_table(
        "academy_milestone_remedial_drills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "milestone_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_milestone_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "drill_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_drills.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "priority_order",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("coach_tip", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "milestone_definition_id", "drill_id", name="uq_amrd_milestone_drill"
        ),
        sa.CheckConstraint("priority_order >= 0", name="ck_amrd_priority_nonneg"),
    )
    op.create_index(
        "ix_academy_milestone_remedial_drills_milestone_definition_id",
        "academy_milestone_remedial_drills",
        ["milestone_definition_id"],
    )
    op.create_index(
        "ix_academy_milestone_remedial_drills_drill_id",
        "academy_milestone_remedial_drills",
        ["drill_id"],
    )

    op.create_table(
        "academy_student_drill_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_auth_id", sa.String(160), nullable=False),
        sa.Column(
            "enrollment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("enrollments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "drill_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_drill_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "milestone_definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("academy_milestone_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_by_auth_id", sa.String(160), nullable=False),
        sa.Column("assigned_by_role", sa.String(20), nullable=False),
        sa.Column("focal_cue_note", sa.Text(), nullable=True),
        sa.Column("sets", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("distance_m", sa.Numeric(8, 2), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="assigned",
        ),
        sa.Column("practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assigned_by_role IN ('coach','admin','strokelab_ai')",
            name="ck_asda_assigned_by_role",
        ),
        sa.CheckConstraint(
            "status IN ('assigned','practiced','mastered','archived')",
            name="ck_asda_status",
        ),
        sa.CheckConstraint("sets > 0", name="ck_asda_sets_positive"),
        sa.CheckConstraint(
            "distance_m IS NULL OR distance_m > 0",
            name="ck_asda_distance_positive",
        ),
    )
    op.create_index(
        "ix_academy_student_drill_assignments_member_auth_id",
        "academy_student_drill_assignments",
        ["member_auth_id"],
    )
    op.create_index(
        "ix_academy_student_drill_assignments_enrollment_id",
        "academy_student_drill_assignments",
        ["enrollment_id"],
    )
    op.create_index(
        "ix_academy_student_drill_assignments_drill_version_id",
        "academy_student_drill_assignments",
        ["drill_version_id"],
    )
    op.create_index(
        "ix_asda_member_auth_status",
        "academy_student_drill_assignments",
        ["member_auth_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_asda_member_auth_status",
        table_name="academy_student_drill_assignments",
    )
    op.drop_index(
        "ix_academy_student_drill_assignments_drill_version_id",
        table_name="academy_student_drill_assignments",
    )
    op.drop_index(
        "ix_academy_student_drill_assignments_enrollment_id",
        table_name="academy_student_drill_assignments",
    )
    op.drop_index(
        "ix_academy_student_drill_assignments_member_auth_id",
        table_name="academy_student_drill_assignments",
    )
    op.drop_table("academy_student_drill_assignments")

    op.drop_index(
        "ix_academy_milestone_remedial_drills_drill_id",
        table_name="academy_milestone_remedial_drills",
    )
    op.drop_index(
        "ix_academy_milestone_remedial_drills_milestone_definition_id",
        table_name="academy_milestone_remedial_drills",
    )
    op.drop_table("academy_milestone_remedial_drills")

    op.drop_constraint(
        "ck_acv_source_provenance",
        "academy_competency_verifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_acv_assessor_role",
        "academy_competency_verifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_acv_source_type",
        "academy_competency_verifications",
        type_="check",
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_review_event_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_progress_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "academy_competency_verifications",
        "source_enrollment_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("academy_competency_verifications", "assessor_role")
    op.drop_column("academy_competency_verifications", "source_type")
