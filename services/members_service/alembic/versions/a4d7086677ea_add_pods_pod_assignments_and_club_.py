"""add_pods_pod_assignments_and_club_default_schedule

Pods moved from sessions_service to members_service in May 2026 — see
docs/club/POD_OPERATIONS.md.

Why this migration owns the drop+recreate (not the parallel
sessions_service drop migration):

  ``scripts/db/reset.sh`` applies migrations in service order, and
  ``members_service`` runs BEFORE ``sessions_service``. If we left the
  drop in sessions_service, members would try to create tables that
  still exist (with the old shape) and fail. Moving the drop here means
  the order works on both fresh and existing databases.

What this migration does:
  1. Drops the old sessions-owned ``pods`` / ``pod_assignments`` tables
     and their enum types (idempotent via IF EXISTS).
  2. Adds the default-session-schedule columns to ``clubs`` (Pod inherits
     these at creation time).
  3. Creates the new members-owned ``pods`` and ``pod_assignments``
     tables with the new shape: ``pod_lead_id`` / ``assistant_pod_lead_id``
     instead of coach refs, plus ``handle``, schedule fields, and proper
     FKs (members and clubs are now in-service).

Autogenerate also captured a couple of unrelated drift items
(``agreement_versions.agreement_type``, ``ix_clubs_slug`` uniqueness).
Those have been intentionally stripped — they belong in a separate
follow-up migration so this one stays focused on the pod move.

Revision ID: a4d7086677ea
Revises: 93f46c4bd1cd
Create Date: 2026-05-10 13:33:02.509661
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a4d7086677ea"
down_revision = "93f46c4bd1cd"
branch_labels = None
depends_on = None


# Reusable ENUM type references. ``create_type=False`` here means: do
# NOT auto-create the type when this Column is added — we manage type
# creation explicitly with the DO blocks below.
_DAY_OF_WEEK = postgresql.ENUM(
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    name="day_of_week_enum",
    create_type=False,
)
_POD_VISIBILITY = postgresql.ENUM(
    "public", "private", name="pod_visibility_enum", create_type=False
)
_POD_STATUS = postgresql.ENUM(
    "active", "inactive", name="pod_status_enum", create_type=False
)
_POD_ASSIGNMENT_SOURCE = postgresql.ENUM(
    "admin",
    "self",
    "lead_transfer",
    name="pod_assignment_source_enum",
    create_type=False,
)


def _create_enum_idempotent(name: str, values: list[str]) -> None:
    """Create a Postgres ENUM type if it doesn't already exist.

    ``Enum.create(checkfirst=True)`` and ``sa.Column(Enum(create_type=False))``
    don't reliably suppress CREATE TYPE under the async psycopg driver, so
    we use a DO/EXCEPTION block which always works."""
    quoted = ", ".join(f"'{v}'" for v in values)
    op.execute(
        f"""
        DO $$ BEGIN
            CREATE TYPE {name} AS ENUM ({quoted});
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )


def upgrade() -> None:
    # ────────────────────────────────────────────────────────────────
    # 1. Drop the old sessions-owned tables (and enums) if they exist.
    #    Also drop day_of_week_enum since this migration is the sole
    #    creator — keeping the drop+recreate idempotent on retry after a
    #    partial failure.
    # ────────────────────────────────────────────────────────────────
    op.execute("DROP TABLE IF EXISTS pod_assignments CASCADE")
    op.execute("DROP TABLE IF EXISTS pods CASCADE")
    op.execute("DROP TYPE IF EXISTS pod_assignment_source_enum")
    op.execute("DROP TYPE IF EXISTS pod_status_enum")
    op.execute("DROP TYPE IF EXISTS pod_visibility_enum")
    # Only drop day_of_week_enum if no table currently uses it. On a
    # fresh DB it doesn't exist; on a partial-retry it might exist
    # without the clubs.default_session_day column being added yet.
    op.execute("DROP TYPE IF EXISTS day_of_week_enum")
    # Belt-and-braces: if a prior partial run added the clubs columns
    # without rolling back, drop them so step 2 can re-add cleanly.
    op.execute("ALTER TABLE clubs DROP COLUMN IF EXISTS default_session_day")
    op.execute("ALTER TABLE clubs DROP COLUMN IF EXISTS default_session_time")
    op.execute(
        "ALTER TABLE clubs DROP COLUMN IF EXISTS default_session_duration_minutes"
    )
    op.execute("ALTER TABLE clubs DROP COLUMN IF EXISTS default_pool_id")

    # ────────────────────────────────────────────────────────────────
    # 2. Create all the ENUM types we need (idempotent), then add the
    #    Club default-session-schedule columns. Pods inherit these at
    #    creation; admins can override per-pod.
    # ────────────────────────────────────────────────────────────────
    _create_enum_idempotent(
        "day_of_week_enum",
        ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    )
    _create_enum_idempotent("pod_visibility_enum", ["public", "private"])
    _create_enum_idempotent("pod_status_enum", ["active", "inactive"])
    _create_enum_idempotent(
        "pod_assignment_source_enum", ["admin", "self", "lead_transfer"]
    )

    op.add_column(
        "clubs",
        sa.Column(
            "default_session_day",
            _DAY_OF_WEEK,
            server_default="sat",
            nullable=False,
        ),
    )
    op.add_column(
        "clubs",
        sa.Column(
            "default_session_time",
            sa.Time(),
            server_default="09:00",
            nullable=False,
        ),
    )
    op.add_column(
        "clubs",
        sa.Column(
            "default_session_duration_minutes",
            sa.Integer(),
            server_default="180",
            nullable=False,
        ),
    )
    op.add_column(
        "clubs",
        sa.Column("default_pool_id", sa.UUID(), nullable=True),
    )

    # ────────────────────────────────────────────────────────────────
    # 3. Create the new members-owned pods table.
    # ────────────────────────────────────────────────────────────────
    op.create_table(
        "pods",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("handle", sa.String(length=60), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("pod_lead_id", sa.UUID(), nullable=False),
        sa.Column("assistant_pod_lead_id", sa.UUID(), nullable=True),
        sa.Column("min_size", sa.Integer(), server_default="2", nullable=False),
        sa.Column("max_size", sa.Integer(), server_default="5", nullable=False),
        sa.Column("default_session_day", _DAY_OF_WEEK, nullable=False),
        sa.Column("default_session_time", sa.Time(), nullable=False),
        sa.Column(
            "default_session_duration_minutes",
            sa.Integer(),
            server_default="180",
            nullable=False,
        ),
        sa.Column("default_pool_id", sa.UUID(), nullable=True),
        sa.Column(
            "visibility",
            _POD_VISIBILITY,
            server_default="public",
            nullable=False,
        ),
        sa.Column(
            "status",
            _POD_STATUS,
            server_default="active",
            nullable=False,
        ),
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dissolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["pod_lead_id"], ["members.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_pod_lead_id"], ["members.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("club_id", "slug", name="uq_pods_club_slug"),
    )
    op.create_index("ix_pods_club_id", "pods", ["club_id"], unique=False)
    op.create_index("ix_pods_pod_lead_id", "pods", ["pod_lead_id"], unique=False)
    op.create_index(
        "uq_pods_club_handle",
        "pods",
        ["club_id", "handle"],
        unique=True,
        postgresql_where="handle IS NOT NULL",
    )
    op.create_index("ix_pods_club_status", "pods", ["club_id", "status"], unique=False)
    op.create_index("ix_pods_directory", "pods", ["visibility", "status"], unique=False)
    op.create_index("ix_pods_review_due", "pods", ["review_due_at"], unique=False)

    # ────────────────────────────────────────────────────────────────
    # 4. Create the new members-owned pod_assignments table.
    # ────────────────────────────────────────────────────────────────
    op.create_table(
        "pod_assignments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pod_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", _POD_ASSIGNMENT_SOURCE, nullable=False),
        sa.Column("assigned_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pod_assignments_member_id",
        "pod_assignments",
        ["member_id"],
        unique=False,
    )
    op.create_index(
        "uq_pod_assignments_one_active_per_member",
        "pod_assignments",
        ["member_id"],
        unique=True,
        postgresql_where="left_at IS NULL",
    )
    op.create_index(
        "ix_pod_assignments_active_per_pod",
        "pod_assignments",
        ["pod_id"],
        unique=False,
        postgresql_where="left_at IS NULL",
    )


def downgrade() -> None:
    # Drop the new members-owned tables.
    op.drop_index(
        "ix_pod_assignments_active_per_pod",
        table_name="pod_assignments",
        postgresql_where="left_at IS NULL",
    )
    op.drop_index(
        "uq_pod_assignments_one_active_per_member",
        table_name="pod_assignments",
        postgresql_where="left_at IS NULL",
    )
    op.drop_index("ix_pod_assignments_member_id", table_name="pod_assignments")
    op.drop_table("pod_assignments")

    op.drop_index("ix_pods_review_due", table_name="pods")
    op.drop_index("ix_pods_directory", table_name="pods")
    op.drop_index("ix_pods_club_status", table_name="pods")
    op.drop_index(
        "uq_pods_club_handle",
        table_name="pods",
        postgresql_where="handle IS NOT NULL",
    )
    op.drop_index("ix_pods_pod_lead_id", table_name="pods")
    op.drop_index("ix_pods_club_id", table_name="pods")
    op.drop_table("pods")

    # Drop new enums.
    op.execute("DROP TYPE IF EXISTS pod_assignment_source_enum")
    op.execute("DROP TYPE IF EXISTS pod_status_enum")
    op.execute("DROP TYPE IF EXISTS pod_visibility_enum")

    # Drop the Club default-session-schedule columns.
    op.drop_column("clubs", "default_pool_id")
    op.drop_column("clubs", "default_session_duration_minutes")
    op.drop_column("clubs", "default_session_time")
    op.drop_column("clubs", "default_session_day")
    op.execute("DROP TYPE IF EXISTS day_of_week_enum")

    # Note: we don't recreate the old sessions-owned pods/pod_assignments
    # tables here. If a full roll-back is needed, also downgrade
    # sessions_service to before 5ca7cf6e4ead — that revision's
    # downgrade() recreates the tables in their old shape.
