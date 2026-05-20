"""add_override_fields_to_milestone_review_events

Hand-written migration — Alembic autogenerate cannot reliably represent
an enum-label addition (``ALTER TYPE ... ADD VALUE``) interleaved with
column additions in one upgrade path. Authors a new ``OVERRIDE`` label
on ``milestone_event_type_enum`` and adds three columns to
``milestone_review_events``:

* ``override_of_event_id`` — self-FK forming the override chain.
* ``override_reason`` — TEXT, required at the API layer for OVERRIDE rows.
* ``ai_metadata`` — JSONB for AI-driven override metadata.

See ``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §5.3 and §6.1 for
the design rationale.

Revision ID: 6c2aaedaed39
Revises: e946432f8fb7
Create Date: 2026-05-20 12:32:43.595845
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6c2aaedaed39"
# Chained off the uppercase-cleanup migration (PR #173 / e946432f8fb7)
# rather than directly off ba3150f4c374 — the cleanup recreates several
# enums in the same transactional path, and chaining this migration
# after it avoids a multi-head split on develop.
down_revision = "e946432f8fb7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Extend the event-type enum with OVERRIDE ──
    # ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction
    # block, so use Alembic's autocommit block.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE milestone_event_type_enum ADD VALUE IF NOT EXISTS 'override'"
        )

    # ── 2. Add override columns to milestone_review_events ──
    op.add_column(
        "milestone_review_events",
        sa.Column(
            "override_of_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_mre_override_of_event_id",
        "milestone_review_events",
        "milestone_review_events",
        ["override_of_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_mre_override_of_event_id",
        "milestone_review_events",
        ["override_of_event_id"],
    )

    op.add_column(
        "milestone_review_events",
        sa.Column("override_reason", sa.Text(), nullable=True),
    )

    op.add_column(
        "milestone_review_events",
        sa.Column(
            "ai_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # Column drops are reversible; the enum-label addition is not
    # (Postgres has no ``ALTER TYPE ... DROP VALUE``). Downgrade
    # therefore drops the columns but leaves the OVERRIDE label in
    # place — any future re-upgrade is idempotent thanks to
    # ``ADD VALUE IF NOT EXISTS``.
    op.drop_column("milestone_review_events", "ai_metadata")
    op.drop_column("milestone_review_events", "override_reason")
    op.drop_index(
        "ix_mre_override_of_event_id",
        table_name="milestone_review_events",
    )
    op.drop_constraint(
        "fk_mre_override_of_event_id",
        "milestone_review_events",
        type_="foreignkey",
    )
    op.drop_column("milestone_review_events", "override_of_event_id")
