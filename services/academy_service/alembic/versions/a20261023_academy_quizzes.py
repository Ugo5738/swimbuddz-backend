"""Add versioned Academy quizzes, cohort-week assignments and graded attempts.

Revision ID: a20261023quiz
Revises: a20261013del
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261023quiz"
down_revision = "a20261013del"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "academy_quizzes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("programs.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("questions_json", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_academy_quizzes_program_id", "academy_quizzes", ["program_id"])
    op.create_table(
        "academy_quiz_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("cohort_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cohorts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quiz_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_quizzes.id"), nullable=False),
        sa.Column("week_number", sa.Integer(), nullable=False),
        sa.Column("passing_score", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("questions_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("quiz_version", sa.Integer(), nullable=False),
        sa.Column("title_snapshot", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("cohort_id", "quiz_id", "week_number", name="uq_academy_quiz_cohort_week"),
    )
    op.create_index("ix_academy_quiz_assignments_cohort_id", "academy_quiz_assignments", ["cohort_id"])
    op.create_table(
        "academy_quiz_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("assignment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_quiz_assignments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("answers_json", postgresql.JSONB(), nullable=False),
        sa.Column("feedback_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("score_percent", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assignment_id", "enrollment_id", "attempt_number", name="uq_academy_quiz_attempt_number"),
    )
    op.create_index("ix_academy_quiz_attempts_assignment_id", "academy_quiz_attempts", ["assignment_id"])
    op.create_index("ix_academy_quiz_attempts_enrollment_id", "academy_quiz_attempts", ["enrollment_id"])


def downgrade():
    op.drop_index("ix_academy_quiz_attempts_enrollment_id", table_name="academy_quiz_attempts")
    op.drop_index("ix_academy_quiz_attempts_assignment_id", table_name="academy_quiz_attempts")
    op.drop_table("academy_quiz_attempts")
    op.drop_index("ix_academy_quiz_assignments_cohort_id", table_name="academy_quiz_assignments")
    op.drop_table("academy_quiz_assignments")
    op.drop_index("ix_academy_quizzes_program_id", table_name="academy_quizzes")
    op.drop_table("academy_quizzes")
