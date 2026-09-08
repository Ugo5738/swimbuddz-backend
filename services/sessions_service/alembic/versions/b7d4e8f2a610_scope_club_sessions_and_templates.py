"""scope Club sessions and templates to a Club

Revision ID: b7d4e8f2a610
Revises: a3c7e9f1b502
Create Date: 2026-09-08

Adds the stable Club owner independently of the optional Pod audience. Existing
Pod-scoped rows are backfilled exactly; general Club rows are inferred only
when their pool is the unique default pool for one Club. Unresolved legacy rows
remain nullable so the migration is safe and can be corrected in the admin UI.
"""

import sqlalchemy as sa
from alembic import op


revision = "b7d4e8f2a610"
down_revision = "a3c7e9f1b502"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("club_id", sa.UUID(), nullable=True))
    op.create_index("ix_sessions_club_id", "sessions", ["club_id"], unique=False)
    op.add_column("session_templates", sa.Column("club_id", sa.UUID(), nullable=True))
    op.create_index(
        "ix_session_templates_club_id",
        "session_templates",
        ["club_id"],
        unique=False,
    )

    # Pod identity is authoritative: every Pod already belongs to one Club.
    op.execute(
        """
        UPDATE sessions AS session
        SET club_id = pod.club_id
        FROM pods AS pod
        WHERE session.pod_id = pod.id
          AND session.club_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE session_templates AS template
        SET club_id = pod.club_id
        FROM pods AS pod
        WHERE template.pod_id = pod.id
          AND template.club_id IS NULL
        """
    )

    # For general Club records, infer ownership only when one Club uniquely
    # claims the selected pool as its default. Ambiguous pools are untouched.
    op.execute(
        """
        WITH unique_pool_clubs AS (
            SELECT default_pool_id, MIN(id::text)::uuid AS club_id
            FROM clubs
            WHERE default_pool_id IS NOT NULL
            GROUP BY default_pool_id
            HAVING COUNT(*) = 1
        )
        UPDATE sessions AS session
        SET club_id = candidate.club_id
        FROM unique_pool_clubs AS candidate
        WHERE session.session_type = 'club'
          AND session.pod_id IS NULL
          AND session.club_id IS NULL
          AND session.pool_id = candidate.default_pool_id
        """
    )
    op.execute(
        """
        WITH unique_pool_clubs AS (
            SELECT default_pool_id, MIN(id::text)::uuid AS club_id
            FROM clubs
            WHERE default_pool_id IS NOT NULL
            GROUP BY default_pool_id
            HAVING COUNT(*) = 1
        )
        UPDATE session_templates AS template
        SET club_id = candidate.club_id
        FROM unique_pool_clubs AS candidate
        WHERE template.session_type = 'club'
          AND template.pod_id IS NULL
          AND template.club_id IS NULL
          AND template.pool_id = candidate.default_pool_id
        """
    )

    op.drop_constraint("ck_sessions_discriminator", "sessions", type_="check")
    op.create_check_constraint(
        "ck_sessions_discriminator",
        "sessions",
        "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
        "AND event_id IS NULL AND club_id IS NULL AND pod_id IS NULL) "
        "OR (session_type = 'event' AND event_id IS NOT NULL "
        "AND cohort_id IS NULL AND club_id IS NULL AND pod_id IS NULL) "
        "OR (session_type = 'club' AND cohort_id IS NULL AND event_id IS NULL) "
        "OR (session_type = 'community' AND cohort_id IS NULL "
        "AND event_id IS NULL AND club_id IS NULL AND pod_id IS NULL)",
    )

    op.drop_constraint(
        "ck_session_templates_pod_only_for_club",
        "session_templates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_session_templates_club_scope",
        "session_templates",
        "session_type = 'club' OR (club_id IS NULL AND pod_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_session_templates_club_scope", "session_templates", type_="check"
    )
    op.create_check_constraint(
        "ck_session_templates_pod_only_for_club",
        "session_templates",
        "pod_id IS NULL OR session_type = 'club'",
    )

    op.drop_constraint("ck_sessions_discriminator", "sessions", type_="check")
    op.create_check_constraint(
        "ck_sessions_discriminator",
        "sessions",
        "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
        "AND event_id IS NULL AND pod_id IS NULL) "
        "OR (session_type = 'event' AND event_id IS NOT NULL "
        "AND cohort_id IS NULL AND pod_id IS NULL) "
        "OR (session_type = 'club' AND cohort_id IS NULL AND event_id IS NULL) "
        "OR (session_type = 'community' AND cohort_id IS NULL "
        "AND event_id IS NULL AND pod_id IS NULL)",
    )

    op.drop_index("ix_session_templates_club_id", table_name="session_templates")
    op.drop_column("session_templates", "club_id")
    op.drop_index("ix_sessions_club_id", table_name="sessions")
    op.drop_column("sessions", "club_id")
