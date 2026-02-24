"""standardize_enum_labels_to_lowercase

Revision ID: b1c2d3e4f501
Revises: e0c0fda5c393
Create Date: 2026-02-22 05:41:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b1c2d3e4f501"
down_revision = "e0c0fda5c393"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add lowercase labels in autocommit mode so they can be used below.
    with op.get_context().autocommit_block():
        for value in ("present", "absent", "late", "excused", "cancelled"):
            op.execute(
                f"ALTER TYPE attendance_status_enum ADD VALUE IF NOT EXISTS '{value}'"
            )
        for value in ("swimmer", "coach", "volunteer", "guest"):
            op.execute(f"ALTER TYPE attendance_role_enum ADD VALUE IF NOT EXISTS '{value}'")

    # Backfill uppercase rows
    op.execute(
        "UPDATE attendance_records SET status = 'present' WHERE status::text = 'PRESENT'"
    )
    op.execute(
        "UPDATE attendance_records SET status = 'absent' WHERE status::text = 'ABSENT'"
    )
    op.execute("UPDATE attendance_records SET status = 'late' WHERE status::text = 'LATE'")
    op.execute(
        "UPDATE attendance_records SET status = 'excused' WHERE status::text = 'EXCUSED'"
    )
    op.execute(
        "UPDATE attendance_records SET status = 'cancelled' WHERE status::text = 'CANCELLED'"
    )

    op.execute(
        "UPDATE attendance_records SET role = 'swimmer' WHERE role::text = 'SWIMMER'"
    )
    op.execute("UPDATE attendance_records SET role = 'coach' WHERE role::text = 'COACH'")
    op.execute(
        "UPDATE attendance_records SET role = 'volunteer' WHERE role::text = 'VOLUNTEER'"
    )
    op.execute("UPDATE attendance_records SET role = 'guest' WHERE role::text = 'GUEST'")


def downgrade() -> None:
    # Intentionally no-op: enum label removal is destructive.
    pass
