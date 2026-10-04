"""add session rates and participants

Revision ID: f6a1b2c3d4e5
Revises: e30550028159
Create Date: 2026-10-04

Additive compatibility migration. Existing Session fee columns and booking /
guest-pass tables remain authoritative fallbacks while the application migrates
onto explicit per-audience rates and canonical session participants.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f6a1b2c3d4e5"
down_revision = "e30550028159"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audience", sa.String(length=24), nullable=False),
        sa.Column("price_mode", sa.String(length=24), server_default="fixed", nullable=False),
        sa.Column("amount_kobo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("audience IN ('club','academy','community','guest')", name="ck_session_rates_audience"),
        sa.CheckConstraint("price_mode IN ('included','fixed','calculated')", name="ck_session_rates_price_mode"),
        sa.CheckConstraint("amount_kobo >= 0", name="ck_session_rates_amount_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "audience", name="uq_session_rate_audience"),
    )
    op.create_index("ix_session_rates_session_id", "session_rates", ["session_id"], unique=False)

    op.create_table(
        "session_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_kind", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_guest_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_pass_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("full_name_snapshot", sa.String(length=160), nullable=False),
        sa.Column("email_snapshot", sa.String(length=320), nullable=True),
        sa.Column("phone_snapshot", sa.String(length=32), nullable=True),
        sa.Column("rate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rate_code", sa.String(length=64), nullable=True),
        sa.Column("fee_amount_kobo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payment_status", sa.String(length=24), server_default="unreconciled", nullable=False),
        sa.Column("waiver_status", sa.String(length=24), server_default="missing", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("participant_kind IN ('member','guest')", name="ck_session_participants_kind"),
        sa.CheckConstraint("source IN ('member_booking','attached_guest','guest_pass','walk_in')", name="ck_session_participants_source"),
        sa.CheckConstraint("payment_status IN ('unreconciled','not_due','pending','paid')", name="ck_session_participants_payment_status"),
        sa.CheckConstraint("waiver_status IN ('accepted','missing','not_required')", name="ck_session_participants_waiver_status"),
        sa.CheckConstraint("fee_amount_kobo >= 0", name="ck_session_participants_fee_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("session_id", "member_id", "booking_id", "booking_guest_id", "guest_pass_id", "email_snapshot", "phone_snapshot"):
        op.create_index(f"ix_session_participants_{column}", "session_participants", [column], unique=False)

    # Backfill explicit rates only where legacy columns already convey an
    # unambiguous commercial meaning. The legacy columns remain untouched.
    op.execute(
        """
        INSERT INTO session_rates
            (id, session_id, audience, price_mode, amount_kobo, label, priority, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'guest', 'fixed', guest_fee_kobo,
               'Guest / non-member rate', 100, now(), now()
        FROM sessions
        WHERE guest_fee_kobo IS NOT NULL
        ON CONFLICT (session_id, audience) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
            (id, session_id, audience, price_mode, amount_kobo, label, priority, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'community', 'fixed', community_dropin_fee_kobo,
               CASE WHEN session_type = 'event' THEN 'Community member rate' ELSE 'Community drop-in rate' END,
               100, now(), now()
        FROM sessions
        WHERE community_dropin_fee_kobo IS NOT NULL
        ON CONFLICT (session_id, audience) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
            (id, session_id, audience, price_mode, amount_kobo, label, priority, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'club', 'fixed', pool_fee,
               CASE WHEN session_type = 'event' THEN 'Club rate' ELSE 'Session member rate' END,
               100, now(), now()
        FROM sessions
        WHERE session_type IN ('club','event')
        ON CONFLICT (session_id, audience) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
            (id, session_id, audience, price_mode, amount_kobo, label, priority, created_at, updated_at)
        SELECT gen_random_uuid(), id, 'academy',
               CASE WHEN session_type = 'cohort_class' AND cohort_fee_mode = 'included' THEN 'included' ELSE 'fixed' END,
               CASE WHEN session_type = 'cohort_class' AND cohort_fee_mode = 'included' THEN 0 ELSE pool_fee END,
               CASE WHEN session_type = 'cohort_class' AND cohort_fee_mode = 'included'
                    THEN 'Included in Academy tuition'
                    ELSE 'Academy rate' END,
               100, now(), now()
        FROM sessions
        WHERE session_type IN ('cohort_class','event')
        ON CONFLICT (session_id, audience) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("session_participants")
    op.drop_table("session_rates")
