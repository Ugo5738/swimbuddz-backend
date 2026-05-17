"""add_cohort_type_and_corporate_program_id

Revision ID: a672e7f12a65
Revises: 7f6b0a2a1c9e
Create Date: 2026-05-17 06:30:57.858279

Adjusted after Alembic autogenerate: the autogen `op.add_column` step
with `sa.Enum(...)` does not always emit a `CREATE TYPE` for the new
Postgres enum type before adding the column, leading to a
"type cohort_type_enum does not exist" error at upgrade time. Fixed by
calling `cohort_type_enum.create(...)` explicitly before
`op.add_column` and `.drop(...)` in downgrade.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a672e7f12a65"
down_revision = "7f6b0a2a1c9e"
branch_labels = None
depends_on = None


_cohort_type_enum = sa.Enum(
    "group",
    "private",
    "small_group",
    "corporate",
    name="cohort_type_enum",
)


def upgrade() -> None:
    # Create the Postgres enum type explicitly. `create_type=False` on the
    # column below stops SQLAlchemy from trying to create it a second time.
    _cohort_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "cohorts",
        sa.Column(
            "type",
            sa.Enum(
                "group",
                "private",
                "small_group",
                "corporate",
                name="cohort_type_enum",
                create_type=False,
            ),
            server_default="group",
            nullable=False,
        ),
    )
    op.add_column(
        "cohorts",
        sa.Column("corporate_program_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        op.f("ix_cohorts_corporate_program_id"),
        "cohorts",
        ["corporate_program_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cohorts_corporate_program_id"), table_name="cohorts")
    op.drop_column("cohorts", "corporate_program_id")
    op.drop_column("cohorts", "type")
    _cohort_type_enum.drop(op.get_bind(), checkfirst=True)
