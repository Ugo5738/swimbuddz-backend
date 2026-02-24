"""standardize_enum_labels_to_lowercase

Revision ID: e1f2a3b4c801
Revises: 863ccd0fcee6
Create Date: 2026-02-22 05:44:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e1f2a3b4c801"
down_revision = "863ccd0fcee6"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "session_type_enum": [
        ("COHORT_CLASS", "cohort_class"),
        ("ONE_ON_ONE", "one_on_one"),
        ("GROUP_BOOKING", "group_booking"),
        ("CLUB", "club"),
        ("COMMUNITY", "community"),
        ("EVENT", "event"),
    ],
    "session_status_enum": [
        ("DRAFT", "draft"),
        ("SCHEDULED", "scheduled"),
        ("IN_PROGRESS", "in_progress"),
        ("COMPLETED", "completed"),
        ("CANCELLED", "cancelled"),
    ],
    "session_location_enum": [
        ("SUNFIT_POOL", "sunfit_pool"),
        ("ROWE_PARK_POOL", "rowe_park_pool"),
        ("FEDERAL_PALACE_POOL", "federal_palace_pool"),
        ("OPEN_WATER", "open_water"),
        ("OTHER", "other"),
    ],
}

COLUMN_ENUM_MAP = [
    ("sessions", "session_type", "session_type_enum"),
    ("sessions", "status", "session_status_enum"),
    ("sessions", "location", "session_location_enum"),
    ("session_templates", "session_type", "session_type_enum"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for enum_type, pairs in ENUM_VALUE_MAP.items():
            for _, new in pairs:
                op.execute(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{new}'")

    for table_name, column_name, enum_type in COLUMN_ENUM_MAP:
        for old, new in ENUM_VALUE_MAP[enum_type]:
            op.execute(
                f"UPDATE {table_name} SET {column_name} = '{new}' WHERE {column_name}::text = '{old}'"
            )

    # Align defaults with lowercase enum labels.
    op.execute("ALTER TABLE sessions ALTER COLUMN session_type SET DEFAULT 'club'")
    op.execute("ALTER TABLE sessions ALTER COLUMN status SET DEFAULT 'scheduled'")


def downgrade() -> None:
    # Intentionally no-op: enum label removal is destructive.
    pass
