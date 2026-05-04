"""add milestone review events

Revision ID: e4a7f2c31d08
Revises: 1baccbd7cd92
Create Date: 2026-04-20 05:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e4a7f2c31d08"
down_revision = "1baccbd7cd92"
branch_labels = None
depends_on = None


MILESTONE_EVENT_TYPE_VALUES = ("claimed", "approved", "rejected", "status_changed")


def upgrade() -> None:
    # Use postgresql.ENUM with create_type=False for the columns; we create
    # the new enum ourselves via raw SQL so SQLAlchemy doesn't also try to
    # create it inside CREATE TABLE. (progress_status_enum already exists.)
    op.execute(
        "CREATE TYPE milestone_event_type_enum AS ENUM "
        "('claimed', 'approved', 'rejected', 'status_changed')"
    )

    event_type_enum = postgresql.ENUM(
        *MILESTONE_EVENT_TYPE_VALUES,
        name="milestone_event_type_enum",
        create_type=False,
    )
    progress_status_enum = postgresql.ENUM(
        "pending",
        "achieved",
        name="progress_status_enum",
        create_type=False,
    )

    op.create_table(
        "milestone_review_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("progress_id", sa.UUID(), nullable=False),
        sa.Column("enrollment_id", sa.UUID(), nullable=False),
        sa.Column("milestone_id", sa.UUID(), nullable=False),
        sa.Column("event_type", event_type_enum, nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("actor_role", sa.String(length=20), nullable=False),
        sa.Column("previous_status", progress_status_enum, nullable=True),
        sa.Column("new_status", progress_status_enum, nullable=False),
        sa.Column("student_notes_snapshot", sa.Text(), nullable=True),
        sa.Column("coach_notes_snapshot", sa.Text(), nullable=True),
        sa.Column("evidence_media_id_snapshot", sa.UUID(), nullable=True),
        sa.Column("score_snapshot", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["progress_id"],
            ["student_progress.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_milestone_review_events_progress_id"),
        "milestone_review_events",
        ["progress_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_milestone_review_events_enrollment_id"),
        "milestone_review_events",
        ["enrollment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_milestone_review_events_milestone_id"),
        "milestone_review_events",
        ["milestone_id"],
        unique=False,
    )

    # --- Backfill seed events from existing StudentProgress rows ---------------
    #
    # For each row with reviewed_at set, insert an "approved" or
    # "status_changed" event so the prior coach feedback is preserved.
    # For each achieved row with achieved_at, also insert a "claimed" event.
    # Use raw SQL so the backfill is deterministic and independent of the ORM.
    op.execute(
        """
        INSERT INTO milestone_review_events (
            id, progress_id, enrollment_id, milestone_id,
            event_type, actor_id, actor_role,
            previous_status, new_status,
            student_notes_snapshot, coach_notes_snapshot,
            evidence_media_id_snapshot, score_snapshot,
            created_at
        )
        SELECT
            gen_random_uuid(),
            sp.id,
            sp.enrollment_id,
            sp.milestone_id,
            CASE
                WHEN sp.status = 'achieved' THEN 'approved'::milestone_event_type_enum
                ELSE 'status_changed'::milestone_event_type_enum
            END,
            COALESCE(sp.reviewed_by_coach_id, gen_random_uuid()),
            'coach',
            NULL::progress_status_enum,
            sp.status,
            NULL,
            sp.coach_notes,
            NULL,
            sp.score,
            sp.reviewed_at
        FROM student_progress sp
        WHERE sp.reviewed_at IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO milestone_review_events (
            id, progress_id, enrollment_id, milestone_id,
            event_type, actor_id, actor_role,
            previous_status, new_status,
            student_notes_snapshot, coach_notes_snapshot,
            evidence_media_id_snapshot, score_snapshot,
            created_at
        )
        SELECT
            gen_random_uuid(),
            sp.id,
            sp.enrollment_id,
            sp.milestone_id,
            'claimed'::milestone_event_type_enum,
            COALESCE(
                (SELECT e.member_auth_id::uuid
                 FROM enrollments e
                 WHERE e.id = sp.enrollment_id
                   AND e.member_auth_id ~ '^[0-9a-fA-F-]{36}$'),
                gen_random_uuid()
            ),
            'student',
            NULL::progress_status_enum,
            'achieved'::progress_status_enum,
            sp.student_notes,
            NULL,
            sp.evidence_media_id,
            NULL,
            sp.achieved_at
        FROM student_progress sp
        WHERE sp.status = 'achieved' AND sp.achieved_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_milestone_review_events_milestone_id"),
        table_name="milestone_review_events",
    )
    op.drop_index(
        op.f("ix_milestone_review_events_enrollment_id"),
        table_name="milestone_review_events",
    )
    op.drop_index(
        op.f("ix_milestone_review_events_progress_id"),
        table_name="milestone_review_events",
    )
    op.drop_table("milestone_review_events")
    op.execute("DROP TYPE IF EXISTS milestone_event_type_enum")
