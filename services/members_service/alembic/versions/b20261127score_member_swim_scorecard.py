"""Add Member Swim Scorecard tables (ADR-026).

Hand-written migration — required because Alembic autogenerate does not
reliably represent explicit CHECK constraints across new domain tables.

Revision ID: b20261127score
Revises: b20261027name
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b20261127score"
down_revision = "b20261027name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_skill_progress",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_code", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column(
            "proficiency",
            sa.String(24),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column(
            "verification_source",
            sa.String(24),
            nullable=False,
            server_default="self_logged",
        ),
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("verified_proficiency", sa.String(24), nullable=True),
        sa.Column(
            "verified_by_member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verified_by_role", sa.String(24), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("member_notes", sa.Text(), nullable=True),
        sa.Column("verifier_notes", sa.Text(), nullable=True),
        sa.Column("last_self_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "member_id",
            "skill_code",
            name="uq_member_skill_progress_member_skill",
        ),
        sa.CheckConstraint(
            "proficiency IN ('not_started', 'learning', 'confident', 'mastered')",
            name="ck_member_skill_progress_proficiency",
        ),
        sa.CheckConstraint(
            "verified_proficiency IS NULL OR verified_proficiency IN "
            "('not_started', 'learning', 'confident', 'mastered')",
            name="ck_member_skill_progress_verified_proficiency",
        ),
        sa.CheckConstraint(
            "domain IN ('foundations', 'freestyle', 'backstroke', "
            "'breaststroke', 'butterfly', 'advanced_skills')",
            name="ck_member_skill_progress_domain",
        ),
        sa.CheckConstraint(
            "verification_source IN ('self_logged', 'strokelab_ai', 'coach_verified')",
            name="ck_member_skill_progress_source",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'pending_review', 'verified', 'rejected')",
            name="ck_member_skill_progress_status",
        ),
        sa.CheckConstraint(
            "verified_by_role IS NULL OR verified_by_role IN ('coach', 'pod_lead', 'admin')",
            name="ck_member_skill_progress_verifier_role",
        ),
    )
    op.create_index(
        "ix_member_skill_progress_member_id",
        "member_skill_progress",
        ["member_id"],
    )
    op.create_index(
        "ix_member_skill_progress_review_queue",
        "member_skill_progress",
        ["verification_status", "updated_at"],
    )

    op.create_table(
        "member_swim_benchmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stroke", sa.String(24), nullable=False),
        sa.Column("benchmark_type", sa.String(24), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("pace_per_100m_seconds", sa.Float(), nullable=True),
        sa.Column("stroke_count", sa.Integer(), nullable=True),
        sa.Column("stroke_rate_spm", sa.Float(), nullable=True),
        sa.Column("swolf_score", sa.Float(), nullable=True),
        sa.Column("pool_length_m", sa.Float(), nullable=True),
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pool_name", sa.String(160), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("benchmark_day_label", sa.String(160), nullable=True),
        sa.Column(
            "verification_source",
            sa.String(24),
            nullable=False,
            server_default="self_logged",
        ),
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column(
            "verified_by_member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("verified_by_role", sa.String(24), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("verifier_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stroke IN ('freestyle', 'backstroke', 'breaststroke', "
            "'butterfly', 'medley', 'open_water', 'any')",
            name="ck_member_swim_benchmarks_stroke",
        ),
        sa.CheckConstraint(
            "benchmark_type IN ('max_distance', 'time_trial', 'spl', "
            "'stroke_rate', 'swolf', 'open_water')",
            name="ck_member_swim_benchmarks_type",
        ),
        sa.CheckConstraint(
            "verification_source IN ('self_logged', 'strokelab_ai', 'coach_verified')",
            name="ck_member_swim_benchmarks_source",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'pending_review', 'verified', 'rejected')",
            name="ck_member_swim_benchmarks_status",
        ),
        sa.CheckConstraint(
            "verified_by_role IS NULL OR verified_by_role IN ('coach', 'pod_lead', 'admin')",
            name="ck_member_swim_benchmarks_verifier_role",
        ),
        sa.CheckConstraint(
            "distance_m IS NULL OR distance_m > 0",
            name="ck_member_swim_benchmarks_distance_pos",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0",
            name="ck_member_swim_benchmarks_duration_pos",
        ),
        sa.CheckConstraint(
            "stroke_count IS NULL OR stroke_count > 0",
            name="ck_member_swim_benchmarks_stroke_count_pos",
        ),
        sa.CheckConstraint(
            "stroke_rate_spm IS NULL OR (stroke_rate_spm > 0 AND stroke_rate_spm <= 180)",
            name="ck_member_swim_benchmarks_spm_range",
        ),
        sa.CheckConstraint(
            "swolf_score IS NULL OR swolf_score > 0",
            name="ck_member_swim_benchmarks_swolf_pos",
        ),
        sa.CheckConstraint(
            "pool_length_m IS NULL OR (pool_length_m >= 5 AND pool_length_m <= 100)",
            name="ck_member_swim_benchmarks_pool_length_range",
        ),
    )
    op.create_index(
        "ix_member_swim_benchmarks_member_id",
        "member_swim_benchmarks",
        ["member_id"],
    )
    op.create_index(
        "ix_member_swim_benchmarks_session_id",
        "member_swim_benchmarks",
        ["session_id"],
    )
    op.create_index(
        "ix_member_swim_benchmarks_member_recorded",
        "member_swim_benchmarks",
        ["member_id", "recorded_at"],
    )
    op.create_index(
        "ix_member_swim_benchmarks_member_type_stroke",
        "member_swim_benchmarks",
        ["member_id", "benchmark_type", "stroke"],
    )
    op.create_index(
        "ix_member_swim_benchmarks_review_queue",
        "member_swim_benchmarks",
        ["verification_status", "created_at"],
    )

    op.create_table(
        "member_scorecard_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("members.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "pod_visibility_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "show_benchmarks_to_pod",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "directory_highlights_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "public_share_card_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("share_token", sa.String(64), nullable=True, unique=True),
        sa.Column(
            "preferred_pool_length_m",
            sa.Float(),
            nullable=True,
            server_default="25.0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "preferred_pool_length_m IS NULL OR "
            "(preferred_pool_length_m >= 5 AND preferred_pool_length_m <= 100)",
            name="ck_member_scorecard_settings_pool_length_range",
        ),
    )
    op.create_index(
        "ix_member_scorecard_settings_member_id",
        "member_scorecard_settings",
        ["member_id"],
    )
    op.create_index(
        "ix_member_scorecard_settings_share_token",
        "member_scorecard_settings",
        ["share_token"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_member_scorecard_settings_share_token",
        table_name="member_scorecard_settings",
    )
    op.drop_index(
        "ix_member_scorecard_settings_member_id",
        table_name="member_scorecard_settings",
    )
    op.drop_table("member_scorecard_settings")

    op.drop_index(
        "ix_member_swim_benchmarks_review_queue",
        table_name="member_swim_benchmarks",
    )
    op.drop_index(
        "ix_member_swim_benchmarks_member_type_stroke",
        table_name="member_swim_benchmarks",
    )
    op.drop_index(
        "ix_member_swim_benchmarks_member_recorded",
        table_name="member_swim_benchmarks",
    )
    op.drop_index(
        "ix_member_swim_benchmarks_session_id",
        table_name="member_swim_benchmarks",
    )
    op.drop_index(
        "ix_member_swim_benchmarks_member_id",
        table_name="member_swim_benchmarks",
    )
    op.drop_table("member_swim_benchmarks")

    op.drop_index(
        "ix_member_skill_progress_review_queue",
        table_name="member_skill_progress",
    )
    op.drop_index(
        "ix_member_skill_progress_member_id",
        table_name="member_skill_progress",
    )
    op.drop_table("member_skill_progress")
