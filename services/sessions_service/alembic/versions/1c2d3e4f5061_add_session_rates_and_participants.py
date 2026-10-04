"""add session rates and participants

Revision ID: 1c2d3e4f5061
Revises: e30550028159
Create Date: 2026-10-04

Additive first phase of the Session commercial/participant refactor.
Legacy Session pricing columns remain in place and are backfilled into
source='legacy_bridge' rate rows so old and new code can run together.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "1c2d3e4f5061"
down_revision = "e30550028159"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("audience", sa.String(24), nullable=False),
        sa.Column("access_source", sa.String(40), nullable=True),
        sa.Column("rate_code", sa.String(64), nullable=False),
        sa.Column("rate_mode", sa.String(16), server_default="fixed", nullable=False),
        sa.Column("amount_kobo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source", sa.String(24), server_default="legacy_bridge", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "rate_code", name="uq_session_rates_code"),
        sa.CheckConstraint(
            "audience IN ('club','academy','community','guest')",
            name="ck_session_rates_audience",
        ),
        sa.CheckConstraint(
            "rate_mode IN ('included','fixed','calculated')",
            name="ck_session_rates_mode",
        ),
        sa.CheckConstraint("amount_kobo >= 0", name="ck_session_rates_amount_nonnegative"),
    )
    op.create_index("ix_session_rates_session_id", "session_rates", ["session_id"])
    op.create_index(
        "ix_session_rates_lookup",
        "session_rates",
        ["session_id", "audience", "access_source", "is_active", "priority"],
    )

    op.add_column("session_bookings", sa.Column("pricing_audience", sa.String(24), nullable=True))
    op.add_column("session_bookings", sa.Column("pricing_source", sa.String(32), nullable=True))
    op.add_column("session_bookings", sa.Column("rate_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("session_bookings", sa.Column("rate_code", sa.String(64), nullable=True))
    op.create_index("ix_session_bookings_rate_id", "session_bookings", ["rate_id"])

    op.create_table(
        "session_participants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("participant_kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("booking_guest_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guest_pass_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("converted_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("full_name_snapshot", sa.String(160), nullable=False),
        sa.Column("email_snapshot", sa.String(320), nullable=True),
        sa.Column("phone_snapshot", sa.String(32), nullable=True),
        sa.Column("audience", sa.String(24), nullable=True),
        sa.Column("access_source", sa.String(40), nullable=True),
        sa.Column(
            "rate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("session_rates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rate_code", sa.String(64), nullable=True),
        sa.Column("fee_amount_kobo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payment_status", sa.String(24), server_default="unknown", nullable=False),
        sa.Column("waiver_status", sa.String(24), server_default="unknown", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "member_id", name="uq_session_participants_member"),
        sa.UniqueConstraint(
            "session_id", "booking_guest_id", name="uq_session_participants_booking_guest"
        ),
        sa.UniqueConstraint(
            "session_id", "guest_pass_id", name="uq_session_participants_guest_pass"
        ),
        sa.CheckConstraint(
            "participant_kind IN ('member','guest')",
            name="ck_session_participants_kind",
        ),
        sa.CheckConstraint(
            "source IN ('member_booking','attached_guest','guest_pass','walk_in')",
            name="ck_session_participants_source",
        ),
        sa.CheckConstraint(
            "payment_status IN ('included','unpaid','paid','waived','unknown')",
            name="ck_session_participants_payment_status",
        ),
        sa.CheckConstraint(
            "waiver_status IN ('accepted','missing','not_required','unknown')",
            name="ck_session_participants_waiver_status",
        ),
        sa.CheckConstraint(
            "fee_amount_kobo >= 0",
            name="ck_session_participants_fee_nonnegative",
        ),
        sa.CheckConstraint(
            "(participant_kind = 'member' AND member_id IS NOT NULL) "
            "OR (participant_kind = 'guest' AND member_id IS NULL)",
            name="ck_session_participants_member_kind",
        ),
    )
    for column in (
        "session_id",
        "member_id",
        "booking_id",
        "booking_guest_id",
        "guest_pass_id",
        "converted_member_id",
        "rate_id",
    ):
        op.create_index(f"ix_session_participants_{column}", "session_participants", [column])
    op.create_index(
        "ix_session_participants_session_source",
        "session_participants",
        ["session_id", "source"],
    )
    op.create_index(
        "ix_session_participants_phone",
        "session_participants",
        ["phone_snapshot"],
    )

    # Backfill the commercial bridge. UUIDs are generated in PostgreSQL without
    # requiring pgcrypto by composing md5 into UUID text.
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':community'),1,8)||'-'||substr(md5(s.id::text || ':community'),9,4)||'-4'||substr(md5(s.id::text || ':community'),14,3)||'-a'||substr(md5(s.id::text || ':community'),18,3)||'-'||substr(md5(s.id::text || ':community'),21,12))::uuid,
          s.id,
          'community',
          CASE WHEN s.session_type = 'club' THEN 'community_dropin' ELSE NULL END,
          CASE
            WHEN s.session_type = 'club' THEN 'community_dropin'
            WHEN s.session_type = 'event' THEN 'event_community'
            ELSE 'community_member'
          END,
          'fixed',
          CASE
            WHEN s.session_type IN ('club','event') AND s.community_dropin_fee_kobo IS NOT NULL
              THEN s.community_dropin_fee_kobo
            ELSE s.pool_fee
          END,
          CASE
            WHEN s.session_type = 'club' THEN 'Community drop-in rate'
            WHEN s.session_type = 'event' THEN 'Community member rate'
            ELSE 'Community member rate'
          END,
          100, true, 'legacy_bridge', now(), now()
        FROM sessions s
        WHERE s.session_type IN ('community','event')
           OR (s.session_type = 'club' AND s.allows_community_dropins = true AND s.community_dropin_fee_kobo IS NOT NULL)
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':guest'),1,8)||'-'||substr(md5(s.id::text || ':guest'),9,4)||'-4'||substr(md5(s.id::text || ':guest'),14,3)||'-a'||substr(md5(s.id::text || ':guest'),18,3)||'-'||substr(md5(s.id::text || ':guest'),21,12))::uuid,
          s.id, 'guest', NULL, 'guest', 'fixed', s.guest_fee_kobo,
          'Guest / non-member rate', 100, true, 'legacy_bridge', now(), now()
        FROM sessions s
        WHERE s.guest_fee_kobo IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':academy'),1,8)||'-'||substr(md5(s.id::text || ':academy'),9,4)||'-4'||substr(md5(s.id::text || ':academy'),14,3)||'-a'||substr(md5(s.id::text || ':academy'),18,3)||'-'||substr(md5(s.id::text || ':academy'),21,12))::uuid,
          s.id, 'academy', NULL,
          CASE WHEN s.session_type='event' THEN 'event_academy'
               WHEN s.cohort_fee_mode='paid_extra' THEN 'academy_extra'
               ELSE 'academy_included' END,
          CASE WHEN s.session_type='cohort_class' AND s.cohort_fee_mode <> 'paid_extra'
               THEN 'included' ELSE 'fixed' END,
          CASE WHEN s.session_type='cohort_class' AND s.cohort_fee_mode <> 'paid_extra'
               THEN 0 ELSE s.pool_fee END,
          CASE WHEN s.session_type='event' THEN 'Academy event rate'
               WHEN s.cohort_fee_mode='paid_extra' THEN 'Extra cohort class'
               ELSE 'Included in Academy tuition' END,
          100, true, 'legacy_bridge', now(), now()
        FROM sessions s
        WHERE s.session_type IN ('cohort_class','event')
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':club-generic'),1,8)||'-'||substr(md5(s.id::text || ':club-generic'),9,4)||'-4'||substr(md5(s.id::text || ':club-generic'),14,3)||'-a'||substr(md5(s.id::text || ':club-generic'),18,3)||'-'||substr(md5(s.id::text || ':club-generic'),21,12))::uuid,
          s.id, 'club', NULL,
          CASE WHEN s.session_type='event' THEN 'event_club' ELSE 'club_member' END,
          'fixed', s.pool_fee,
          CASE WHEN s.session_type='event' THEN 'Club event rate' ELSE 'Club session rate' END,
          100, true, 'legacy_bridge', now(), now()
        FROM sessions s
        WHERE s.session_type IN ('club','event')
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':club-included'),1,8)||'-'||substr(md5(s.id::text || ':club-included'),9,4)||'-4'||substr(md5(s.id::text || ':club-included'),14,3)||'-a'||substr(md5(s.id::text || ':club-included'),18,3)||'-'||substr(md5(s.id::text || ':club-included'),21,12))::uuid,
          s.id, 'club', 'club_enrollment', 'club_included', 'included', 0,
          'Included in Club quarter', 10, true, 'legacy_bridge', now(), now()
        FROM sessions s WHERE s.session_type='club'
        """
    )
    op.execute(
        """
        INSERT INTO session_rates
          (id, session_id, audience, access_source, rate_code, rate_mode,
           amount_kobo, label, priority, is_active, source, created_at, updated_at)
        SELECT
          (substr(md5(s.id::text || ':club-transition'),1,8)||'-'||substr(md5(s.id::text || ':club-transition'),9,4)||'-4'||substr(md5(s.id::text || ':club-transition'),14,3)||'-a'||substr(md5(s.id::text || ':club-transition'),18,3)||'-'||substr(md5(s.id::text || ':club-transition'),21,12))::uuid,
          s.id, 'club', 'club_transition', 'club_transition', 'fixed', s.pool_fee,
          'Transition session rate', 20, true, 'legacy_bridge', now(), now()
        FROM sessions s WHERE s.session_type='club'
        """
    )


def downgrade() -> None:
    op.drop_table("session_participants")
    op.drop_index("ix_session_bookings_rate_id", table_name="session_bookings")
    op.drop_column("session_bookings", "rate_code")
    op.drop_column("session_bookings", "rate_id")
    op.drop_column("session_bookings", "pricing_source")
    op.drop_column("session_bookings", "pricing_audience")
    op.drop_table("session_rates")
