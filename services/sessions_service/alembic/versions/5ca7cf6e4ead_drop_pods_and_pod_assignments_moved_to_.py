"""drop_pods_and_pod_assignments_moved_to_members_service

Marker migration for the May 2026 ownership transfer of the ``pods`` and
``pod_assignments`` tables from ``sessions_service`` to ``members_service``.
See docs/club/POD_OPERATIONS.md.

The actual DROP TABLE statements live in the parallel ``members_service``
migration ``a4d7086677ea_add_pods_pod_assignments_and_club_...`` —
because ``scripts/db/reset.sh`` applies migrations in service order with
``members_service`` running BEFORE ``sessions_service``. If the drop
lived here, members would try to recreate tables that still existed
(with the old shape) and fail. Putting both the drop and the create in
one members migration makes the order work on both fresh and existing
databases.

This file therefore exists only to advance the sessions_service alembic
chain past ``7c13c1913395`` so the metadata stays clean. ``upgrade()``
is a no-op; ``downgrade()`` recreates the old sessions-owned tables for
emergency rollback (matched by members_service's own downgrade, which
removes the new tables).

Revision ID: 5ca7cf6e4ead
Revises: 7c13c1913395
Create Date: 2026-05-10 13:28:13.691738
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "5ca7cf6e4ead"
down_revision = "7c13c1913395"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op. The drop happens in members_service migration
    # a4d7086677ea, which runs first per scripts/db/reset.sh ordering.
    pass


def downgrade() -> None:
    # Recreate the original sessions_service-owned tables. Mirrors the
    # 7c13c1913395_add_pods_and_pod_assignments migration so a downgrade
    # round-trips. Cross-service refs (club_id, lead_coach_id) match the
    # old shape — no FKs were enforced.
    import sqlalchemy as sa

    op.create_table(
        "pods",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("club_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lead_coach_id", sa.UUID(), nullable=False),
        sa.Column("assistant_coach_id", sa.UUID(), nullable=True),
        sa.Column("min_size", sa.Integer(), server_default="2", nullable=False),
        sa.Column("max_size", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "visibility",
            sa.Enum("public", "private", name="pod_visibility_enum"),
            server_default="public",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="pod_status_enum"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dissolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("club_id", "slug", name="uq_pods_club_slug"),
    )
    op.create_index(op.f("ix_pods_club_id"), "pods", ["club_id"], unique=False)
    op.create_index("ix_pods_club_status", "pods", ["club_id", "status"], unique=False)
    op.create_index("ix_pods_directory", "pods", ["visibility", "status"], unique=False)
    op.create_index(
        op.f("ix_pods_lead_coach_id"), "pods", ["lead_coach_id"], unique=False
    )
    op.create_index("ix_pods_review_due", "pods", ["review_due_at"], unique=False)

    op.create_table(
        "pod_assignments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pod_id", sa.UUID(), nullable=False),
        sa.Column("member_id", sa.UUID(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assigned_by",
            sa.Enum(
                "admin", "self", "coach_transfer", name="pod_assignment_source_enum"
            ),
            nullable=False,
        ),
        sa.Column("assigned_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pod_assignments_active_per_pod",
        "pod_assignments",
        ["pod_id"],
        unique=False,
        postgresql_where="left_at IS NULL",
    )
    op.create_index(
        op.f("ix_pod_assignments_member_id"),
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
