"""add_clubs_table

Hand-written because alembic autogen refused to run against a dev DB that
hadn't been upgraded to the Phase 1 head yet (the challenges revamp
migration `57343c61d16e_challenge_revamp_phase1.py` is still pending).
The table here is small and self-contained so the risk of drift between
model + migration is minimal — any future autogen run after this lands
will reconcile if anything is off.

Revision ID: 93f46c4bd1cd
Revises: 57343c61d16e
Create Date: 2026-05-07 02:30:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "93f46c4bd1cd"
down_revision = "57343c61d16e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clubs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_clubs_slug"),
    )
    op.create_index(
        op.f("ix_clubs_slug"), "clubs", ["slug"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_clubs_slug"), table_name="clubs")
    op.drop_table("clubs")
