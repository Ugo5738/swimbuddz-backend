"""create_media_audit_logs_table

Hand-written migration — the table follows the B4 canonical audit-log
shape (``docs/design/B4_AUDIT_LOG_UNIFICATION.md``) so a future
unification PR can adopt the shared mixin without a data migration.
Includes an ``INET`` column which Alembic autogenerate represents
inconsistently across versions; pinning the migration by hand keeps
the column type explicit.

See ``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §4.3.

Revision ID: 7b440e62d536
Revises: 35f4a6b1f40e
Create Date: 2026-05-20 12:32:52.706934
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "7b440e62d536"
down_revision = "35f4a6b1f40e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        # Canonical B4 fields ──
        sa.Column("domain", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("actor_label", sa.String(length=255), nullable=True),
        sa.Column(
            "old_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    # Indexes — pick the columns audit consumers actually filter by.
    # ``actor_id`` is the principal-attribution lookup (especially for
    # the synthetic AI principal, where "all AI activity" is a
    # one-line predicate); ``entity_id`` is the per-media lookup;
    # ``action`` is the filter for "all downloads"; ``domain`` exists
    # for the future unified-view query; ``created_at`` for
    # time-range scans.
    op.create_index(
        "ix_media_audit_logs_actor_id",
        "media_audit_logs",
        ["actor_id"],
    )
    op.create_index(
        "ix_media_audit_logs_entity_id",
        "media_audit_logs",
        ["entity_id"],
    )
    op.create_index(
        "ix_media_audit_logs_action",
        "media_audit_logs",
        ["action"],
    )
    op.create_index(
        "ix_media_audit_logs_domain",
        "media_audit_logs",
        ["domain"],
    )
    op.create_index(
        "ix_media_audit_logs_created_at",
        "media_audit_logs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_media_audit_logs_created_at", table_name="media_audit_logs")
    op.drop_index("ix_media_audit_logs_domain", table_name="media_audit_logs")
    op.drop_index("ix_media_audit_logs_action", table_name="media_audit_logs")
    op.drop_index("ix_media_audit_logs_entity_id", table_name="media_audit_logs")
    op.drop_index("ix_media_audit_logs_actor_id", table_name="media_audit_logs")
    op.drop_table("media_audit_logs")
