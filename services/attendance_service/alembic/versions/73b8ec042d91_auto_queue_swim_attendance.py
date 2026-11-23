"""Automatically queue ordinary attendance changes for post-session rewards.

Revision ID: 73b8ec042d91
Revises: 6af065fd2ff8
"""
from alembic import op

revision = "73b8ec042d91"
down_revision = "6af065fd2ff8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION queue_swim_verification()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS NULL OR NEW.status NOT IN ('present','late')
               OR NEW.role IS DISTINCT FROM 'swimmer' OR NEW.member_id IS NULL THEN
                NEW.swim_verified_on := NULL;
                NEW.swim_verified_by := NULL;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status IN ('present','late') AND NEW.role = 'swimmer'
                   AND NEW.member_id IS NOT NULL THEN
                    NEW.swim_revision := 1;
                    NEW.swim_sync_at := now();
                END IF;
            ELSIF NEW.status IS DISTINCT FROM OLD.status
               OR NEW.role IS DISTINCT FROM OLD.role
               OR NEW.member_id IS DISTINCT FROM OLD.member_id
               OR NEW.swim_verified_on IS DISTINCT FROM OLD.swim_verified_on THEN
                NEW.swim_revision := OLD.swim_revision + 1;
                NEW.swim_sync_at := now();
            END IF;
            RETURN NEW;
        END $$;
    """)
    # Queue existing default attendance (including prebooked future sessions).
    # Worker never auto-credits a session older than the launch cutoff.
    op.execute("""
        UPDATE attendance_records
        SET swim_revision = swim_revision + 1, swim_sync_at = now()
        WHERE status IN ('present','late') AND role = 'swimmer'
          AND member_id IS NOT NULL AND swim_sync_at IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION queue_swim_verification()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IS NULL OR NEW.status NOT IN ('present','late')
               OR NEW.role IS DISTINCT FROM 'swimmer' OR NEW.member_id IS NULL THEN
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
