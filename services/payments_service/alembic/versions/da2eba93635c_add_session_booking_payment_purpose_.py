"""add_session_booking_payment_purpose_enum_value

Revision ID: da2eba93635c
Revises: 8bad74e751fb
Create Date: 2026-05-17 11:30:55.059776

Hand-written migration — Alembic autogenerate cannot represent a Postgres
ENUM value addition. Generated via `./scripts/db/migrate.sh --manual` so
the revision ID is Alembic-assigned and the chain stays intact.

Adds the `session_booking` value to `payment_purpose_enum` for A1 Phase
3.3 Paystack pre-booking (PaymentPurpose.SESSION_BOOKING). `ALTER TYPE
... ADD VALUE` cannot run inside a transaction block on older PG, so
this migration commits the connection first. `IF NOT EXISTS` makes it
idempotent.

Downgrade is intentionally a no-op: Postgres has no `ALTER TYPE ... DROP
VALUE`. Removing the value would require recreating the type and
rewriting every dependent column — not worth it for a downgrade path
that, in practice, is never exercised in prod.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "da2eba93635c"
down_revision = "8bad74e751fb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on PG < 12
    # and Alembic wraps migrations in one. Commit, then run it autonomously.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE payment_purpose_enum ADD VALUE IF NOT EXISTS 'session_booking'"
    )


def downgrade() -> None:
    # No-op — Postgres cannot drop an enum value. See module docstring.
    pass
