"""Expand-only independent Academy curriculum catalog.

Revision ID: a20261122catalog
Revises: a20261025class
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261122catalog"
down_revision = "a20261025class"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academy_milestone_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_academy_milestone_definitions_code"),
    )
    op.create_table(
        "academy_drills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_academy_drills_code"),
    )
    op.create_table(
        "academy_lesson_blueprints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_academy_lesson_blueprints_code"),
    )
    op.create_table(
        "academy_knowledge_quizzes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_academy_knowledge_quizzes_code"),
    )
    op.create_table(
        "academy_modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code", name="uq_academy_modules_code"),
    )
    op.create_table(
        "academy_milestone_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_milestone_definitions.id"), nullable=False),
        sa.Column("rubric_json", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_policy", sa.String(20), nullable=False, server_default="video"),
        sa.Column("assessment_context_json", postgresql.JSONB(), nullable=False),
        sa.Column("safety_gate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("demo_media_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("definition_id", "version", name="uq_amv_definition_version"),
        sa.CheckConstraint("version > 0", name="ck_amv_version_positive"),
        sa.CheckConstraint("state IN ('draft','published')", name="ck_amv_state"),
        sa.CheckConstraint("evidence_policy IN ('video','time_trial','observation')", name="ck_amv_evidence_policy"),
    )
    op.create_table(
        "academy_drill_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_drills.id"), nullable=False),
        sa.Column("steps_json", postgresql.JSONB(), nullable=False),
        sa.Column("safety_json", postgresql.JSONB(), nullable=False),
        sa.Column("primary_focal_cue", sa.Text(), nullable=False),
        sa.Column("requires_supervision", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("demo_media_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.UniqueConstraint("drill_id", "version", name="uq_adv_drill_version"),
        sa.CheckConstraint("version > 0", name="ck_adv_version_positive"),
        sa.CheckConstraint("state IN ('draft','published')", name="ck_adv_state"),
    )
    op.create_table(
        "academy_lesson_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_lesson_blueprints.id"), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("blocks_json", postgresql.JSONB(), nullable=False),
        sa.Column("safety_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("lesson_id", "version", name="uq_alv_lesson_version"),
        sa.CheckConstraint("version > 0", name="ck_alv_version_positive"),
        sa.CheckConstraint("state IN ('draft','published')", name="ck_alv_state"),
        sa.CheckConstraint("duration_minutes > 0", name="ck_alv_duration_positive"),
    )
    op.create_table(
        "academy_knowledge_quiz_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_knowledge_quizzes.id"), nullable=False),
        sa.Column("questions_json", postgresql.JSONB(), nullable=False),
        sa.Column("passing_percent", sa.Integer(), nullable=False, server_default="90"),
        sa.UniqueConstraint("quiz_id", "version", name="uq_akqv_quiz_version"),
        sa.CheckConstraint("version > 0", name="ck_akqv_version_positive"),
        sa.CheckConstraint("state IN ('draft','published')", name="ck_akqv_state"),
        sa.CheckConstraint("passing_percent BETWEEN 1 AND 100", name="ck_akqv_passing_percent"),
    )
    op.create_table(
        "academy_module_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by_auth_id", sa.String(160), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("module_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_modules.id"), nullable=False),
        sa.Column("outcomes_json", postgresql.JSONB(), nullable=False),
        sa.Column("completion_policy_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("module_id", "version", name="uq_amo_module_version"),
        sa.CheckConstraint("version > 0", name="ck_amo_version_positive"),
        sa.CheckConstraint("state IN ('draft','published')", name="ck_amo_state"),
    )
    op.create_table(
        "academy_lesson_drills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lesson_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_lesson_versions.id"), nullable=False),
        sa.Column("drill_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_drill_versions.id"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("sets", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Numeric(8, 2), nullable=True),
        sa.Column("focal_cue_override", sa.Text(), nullable=True),
        sa.UniqueConstraint("lesson_version_id", "drill_version_id", name="uq_ald_lesson_drill"),
        sa.UniqueConstraint("lesson_version_id", "order_index", name="uq_ald_lesson_order"),
        sa.CheckConstraint("sets > 0", name="ck_ald_sets_positive"),
    )
    op.create_table(
        "academy_module_lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_module_versions.id"), nullable=False),
        sa.Column("lesson_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_lesson_versions.id"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.UniqueConstraint("module_version_id", "lesson_version_id", name="uq_aml_module_lesson"),
        sa.UniqueConstraint("module_version_id", "order_index", name="uq_aml_module_order"),
    )
    op.create_table(
        "academy_module_milestones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_module_versions.id"), nullable=False),
        sa.Column("milestone_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_milestone_versions.id"), nullable=False),
        sa.Column("required_for_completion", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("module_version_id", "milestone_version_id", name="uq_amm_module_milestone"),
    )
    op.create_table(
        "academy_module_quizzes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_module_versions.id"), nullable=False),
        sa.Column("quiz_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_knowledge_quiz_versions.id"), nullable=False),
        sa.Column("required_for_completion", sa.Boolean(), nullable=False, server_default="true"),
        sa.UniqueConstraint("module_version_id", "quiz_version_id", name="uq_amq_module_quiz"),
    )
    op.create_table(
        "academy_program_modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("module_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_module_versions.id"), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.UniqueConstraint("program_id", "module_version_id", name="uq_apm_program_module"),
        sa.UniqueConstraint("program_id", "order_index", name="uq_apm_program_order"),
    )
    op.create_table(
        "academy_program_milestone_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("milestone_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_milestone_versions.id"), nullable=False),
        sa.Column("required_for_completion", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("target_week", sa.Integer(), nullable=True),
        sa.UniqueConstraint("program_id", "milestone_version_id", name="uq_apma_program_milestone"),
    )
    op.create_table(
        "academy_cohort_milestone_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cohorts.id"), nullable=False),
        sa.Column("milestone_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_milestone_versions.id"), nullable=False),
        sa.Column("target_week", sa.Integer(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=False),
        sa.Column("assigned_by_auth_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_id", "milestone_version_id", name="uq_acma_cohort_milestone"),
    )
    op.create_table(
        "academy_milestone_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("milestone_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_milestone_definitions.id"), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skills.id"), nullable=False),
        sa.UniqueConstraint("milestone_id", "skill_id", name="uq_ams_milestone_skill"),
    )
    op.create_table(
        "academy_drill_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("drill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_drills.id"), nullable=False),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("skills.id"), nullable=False),
        sa.UniqueConstraint("drill_id", "skill_id", name="uq_ads_drill_skill"),
    )
    op.create_index("ix_academy_milestone_definitions_code", "academy_milestone_definitions", ["code"])
    op.create_index("ix_academy_drills_code", "academy_drills", ["code"])
    op.create_index("ix_academy_lesson_blueprints_code", "academy_lesson_blueprints", ["code"])
    op.create_index("ix_academy_knowledge_quizzes_code", "academy_knowledge_quizzes", ["code"])
    op.create_index("ix_academy_modules_code", "academy_modules", ["code"])
    op.create_index("ix_academy_milestone_versions_definition_id", "academy_milestone_versions", ["definition_id"])
    op.create_index("ix_academy_drill_versions_drill_id", "academy_drill_versions", ["drill_id"])
    op.create_index("ix_academy_lesson_versions_lesson_id", "academy_lesson_versions", ["lesson_id"])
    op.create_index("ix_academy_knowledge_quiz_versions_quiz_id", "academy_knowledge_quiz_versions", ["quiz_id"])
    op.create_index("ix_academy_module_versions_module_id", "academy_module_versions", ["module_id"])
    op.create_index("ix_academy_lesson_drills_lesson_version_id", "academy_lesson_drills", ["lesson_version_id"])
    op.create_index("ix_academy_lesson_drills_drill_version_id", "academy_lesson_drills", ["drill_version_id"])
    op.create_index("ix_academy_module_lessons_module_version_id", "academy_module_lessons", ["module_version_id"])
    op.create_index("ix_academy_module_lessons_lesson_version_id", "academy_module_lessons", ["lesson_version_id"])
    op.create_index("ix_academy_module_milestones_module_version_id", "academy_module_milestones", ["module_version_id"])
    op.create_index("ix_academy_module_milestones_milestone_version_id", "academy_module_milestones", ["milestone_version_id"])
    op.create_index("ix_academy_module_quizzes_module_version_id", "academy_module_quizzes", ["module_version_id"])
    op.create_index("ix_academy_module_quizzes_quiz_version_id", "academy_module_quizzes", ["quiz_version_id"])
    op.create_index("ix_academy_program_modules_program_id", "academy_program_modules", ["program_id"])
    op.create_index("ix_academy_program_modules_module_version_id", "academy_program_modules", ["module_version_id"])
    op.create_index("ix_academy_program_milestone_assignments_program_id", "academy_program_milestone_assignments", ["program_id"])
    op.create_index("ix_academy_program_milestone_assignments_milestone_version_id", "academy_program_milestone_assignments", ["milestone_version_id"])
    op.create_index("ix_academy_cohort_milestone_assignments_cohort_id", "academy_cohort_milestone_assignments", ["cohort_id"])
    op.create_index("ix_academy_cohort_milestone_assignments_milestone_version_id", "academy_cohort_milestone_assignments", ["milestone_version_id"])
    op.create_index("ix_academy_milestone_skills_milestone_id", "academy_milestone_skills", ["milestone_id"])
    op.create_index("ix_academy_milestone_skills_skill_id", "academy_milestone_skills", ["skill_id"])
    op.create_index("ix_academy_drill_skills_drill_id", "academy_drill_skills", ["drill_id"])
    op.create_index("ix_academy_drill_skills_skill_id", "academy_drill_skills", ["skill_id"])


def downgrade() -> None:
    op.drop_index("ix_academy_drill_skills_skill_id", table_name="academy_drill_skills")
    op.drop_index("ix_academy_drill_skills_drill_id", table_name="academy_drill_skills")
    op.drop_index("ix_academy_milestone_skills_skill_id", table_name="academy_milestone_skills")
    op.drop_index("ix_academy_milestone_skills_milestone_id", table_name="academy_milestone_skills")
    op.drop_index("ix_academy_cohort_milestone_assignments_milestone_version_id", table_name="academy_cohort_milestone_assignments")
    op.drop_index("ix_academy_cohort_milestone_assignments_cohort_id", table_name="academy_cohort_milestone_assignments")
    op.drop_index("ix_academy_program_milestone_assignments_milestone_version_id", table_name="academy_program_milestone_assignments")
    op.drop_index("ix_academy_program_milestone_assignments_program_id", table_name="academy_program_milestone_assignments")
    op.drop_index("ix_academy_program_modules_module_version_id", table_name="academy_program_modules")
    op.drop_index("ix_academy_program_modules_program_id", table_name="academy_program_modules")
    op.drop_index("ix_academy_module_quizzes_quiz_version_id", table_name="academy_module_quizzes")
    op.drop_index("ix_academy_module_quizzes_module_version_id", table_name="academy_module_quizzes")
    op.drop_index("ix_academy_module_milestones_milestone_version_id", table_name="academy_module_milestones")
    op.drop_index("ix_academy_module_milestones_module_version_id", table_name="academy_module_milestones")
    op.drop_index("ix_academy_module_lessons_lesson_version_id", table_name="academy_module_lessons")
    op.drop_index("ix_academy_module_lessons_module_version_id", table_name="academy_module_lessons")
    op.drop_index("ix_academy_lesson_drills_drill_version_id", table_name="academy_lesson_drills")
    op.drop_index("ix_academy_lesson_drills_lesson_version_id", table_name="academy_lesson_drills")
    op.drop_index("ix_academy_module_versions_module_id", table_name="academy_module_versions")
    op.drop_index("ix_academy_knowledge_quiz_versions_quiz_id", table_name="academy_knowledge_quiz_versions")
    op.drop_index("ix_academy_lesson_versions_lesson_id", table_name="academy_lesson_versions")
    op.drop_index("ix_academy_drill_versions_drill_id", table_name="academy_drill_versions")
    op.drop_index("ix_academy_milestone_versions_definition_id", table_name="academy_milestone_versions")
    op.drop_index("ix_academy_modules_code", table_name="academy_modules")
    op.drop_index("ix_academy_knowledge_quizzes_code", table_name="academy_knowledge_quizzes")
    op.drop_index("ix_academy_lesson_blueprints_code", table_name="academy_lesson_blueprints")
    op.drop_index("ix_academy_drills_code", table_name="academy_drills")
    op.drop_index("ix_academy_milestone_definitions_code", table_name="academy_milestone_definitions")
    op.drop_table("academy_drill_skills")
    op.drop_table("academy_milestone_skills")
    op.drop_table("academy_cohort_milestone_assignments")
    op.drop_table("academy_program_milestone_assignments")
    op.drop_table("academy_program_modules")
    op.drop_table("academy_module_quizzes")
    op.drop_table("academy_module_milestones")
    op.drop_table("academy_module_lessons")
    op.drop_table("academy_lesson_drills")
    op.drop_table("academy_module_versions")
    op.drop_table("academy_knowledge_quiz_versions")
    op.drop_table("academy_lesson_versions")
    op.drop_table("academy_drill_versions")
    op.drop_table("academy_milestone_versions")
    op.drop_table("academy_modules")
    op.drop_table("academy_knowledge_quizzes")
    op.drop_table("academy_lesson_blueprints")
    op.drop_table("academy_drills")
    op.drop_table("academy_milestone_definitions")
