"""drop_aspirational_session_types_and_booking_id

Revision ID: 0cfeb8c4ddfb
Revises: c490b3168c3e
Create Date: 2026-05-17 06:00:33.043423

Hand-written migration — A1 Phase 3.1. Drops the aspirational ONE_ON_ONE
and GROUP_BOOKING SessionType values and the unused `booking_id` column;
rebuilds the discriminator CHECK constraint with the simpler 4-branch
expression. See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md.

Generated via `./scripts/db/migrate.sh --manual sessions_service ...` so
the revision ID is Alembic-assigned and the chain stays intact. Body is
hand-written because Alembic autogenerate cannot represent CHECK
constraint changes (and the column drop is paired with the constraint
swap, which must happen in one transaction).

Verified clean before applying:
  * Zero rows in `sessions` with session_type IN ('one_on_one','group_booking')
  * Zero rows in `sessions` with booking_id IS NOT NULL
  * Zero rows in `session_templates` with session_type IN those values

The Postgres enum type `session_type_enum` is left alone. PG enum values
can be removed but the procedure is awkward (create-new-type, cast all
rows, drop-old). Since the removed values have zero references, leaving
them in the enum is harmless. A future migration can clean them up if we
ever care about the enum surface.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0cfeb8c4ddfb"
down_revision = "c490b3168c3e"
branch_labels = None
depends_on = None


# New 4-branch discriminator expression — matches validate_session_discriminator()
# after Phase 3.1 dropped ONE_ON_ONE / GROUP_BOOKING.
_NEW_CHECK = (
    "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
    "AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'event' AND event_id IS NOT NULL "
    "AND cohort_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'club' "
    "AND cohort_id IS NULL AND event_id IS NULL) "
    "OR (session_type = 'community' "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL)"
)

# Old 6-branch expression preserved here for the downgrade path.
_OLD_CHECK = (
    "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
    "AND event_id IS NULL AND booking_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'event' AND event_id IS NOT NULL "
    "AND cohort_id IS NULL AND booking_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'one_on_one' AND booking_id IS NOT NULL "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'group_booking' AND booking_id IS NOT NULL "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'club' "
    "AND cohort_id IS NULL AND event_id IS NULL AND booking_id IS NULL) "
    "OR (session_type = 'community' "
    "AND cohort_id IS NULL AND event_id IS NULL "
    "AND booking_id IS NULL AND pod_id IS NULL)"
)


def upgrade() -> None:
    # The CHECK constraint references booking_id, so it must be dropped
    # before the column. Order: drop CHECK → drop column → re-add CHECK.
    op.execute("ALTER TABLE sessions DROP CONSTRAINT ck_sessions_discriminator")
    op.execute("ALTER TABLE sessions DROP COLUMN booking_id")
    op.execute(
        f"ALTER TABLE sessions ADD CONSTRAINT ck_sessions_discriminator "
        f"CHECK ({_NEW_CHECK})"
    )


def downgrade() -> None:
    # Restore booking_id column + old 6-branch constraint. Existing rows
    # have NULL booking_id, which satisfies every branch of the old
    # constraint, so no data fix-up needed.
    op.execute("ALTER TABLE sessions DROP CONSTRAINT ck_sessions_discriminator")
    op.execute("ALTER TABLE sessions ADD COLUMN booking_id UUID")
    op.execute("CREATE INDEX ix_sessions_booking_id ON sessions (booking_id)")
    op.execute(
        f"ALTER TABLE sessions ADD CONSTRAINT ck_sessions_discriminator "
        f"CHECK ({_OLD_CHECK})"
    )
