"""queue_verified_swims_for_reward_reconciliation

Hand-written migration — includes the attendance correction/outbox trigger.

Revision ID: 6af065fd2ff8
Revises: c7d8e9f0a123
Create Date: 2026-11-19 22:40:28.016238
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6af065fd2ff8"
down_revision = "c7d8e9f0a123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attendance_records", sa.Column("swim_verified_on", sa.Date()))
    op.add_column("attendance_records", sa.Column("swim_verified_by", sa.String()))
    op.add_column(
        "attendance_records",
        sa.Column("swim_revision", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "attendance_records",
        sa.Column(
            "swim_delivered_revision",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "attendance_records", sa.Column("swim_sync_at", sa.DateTime(timezone=True))
    )
    op.create_index(
        "ix_attendance_swim_sync_at",
        "attendance_records",
        ["swim_sync_at"],
        postgresql_where=sa.text("swim_sync_at IS NOT NULL"),
    )
    # Covers service, member and worker writes, including SQL upserts.
    op.execute("""
        CREATE FUNCTION queue_swim_verification() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS NULL OR NEW.status NOT IN ('present','late') OR NEW.role IS DISTINCT FROM 'swimmer' OR NEW.member_id IS NULL THEN
                NEW.swim_verified_on := NULL;
                NEW.swim_verified_by := NULL;
            END IF;
            IF (TG_OP = 'INSERT' AND NEW.swim_verified_on IS NOT NULL)
               OR (TG_OP = 'UPDATE' AND NEW.swim_verified_on IS DISTINCT FROM OLD.swim_verified_on) THEN
                NEW.swim_revision := COALESCE(OLD.swim_revision, 0) + 1;
                NEW.swim_sync_at := now();
            END IF;
            RETURN NEW;
        END $$;
    """)
    op.execute(
        "CREATE TRIGGER queue_swim_verification BEFORE INSERT OR UPDATE ON attendance_records FOR EACH ROW EXECUTE FUNCTION queue_swim_verification()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER queue_swim_verification ON attendance_records")
    op.execute("DROP FUNCTION queue_swim_verification()")
    op.drop_index("ix_attendance_swim_sync_at", table_name="attendance_records")
    for name in (
        "swim_sync_at",
        "swim_delivered_revision",
        "swim_revision",
        "swim_verified_by",
        "swim_verified_on",
    ):
        op.drop_column("attendance_records", name)
