"""Actual Club schedules and named Community Experience tickets.

Revision ID: d8e0f2a4b615
Revises: c7d5e9f3b842

Do not invent session links, Event links, prices, or historical waivers. Existing
commercial/purchase rows remain unchanged; Admin configures new draft versions.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "d8e0f2a4b615"
down_revision = "c7d5e9f3b842"
branch_labels = None
depends_on = None


def pk():
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def ref(name, target, ondelete="RESTRICT", nullable=False):
    return sa.Column(
        name,
        pg.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
    )


def upgrade():
    op.add_column(
        "club_plan_versions",
        sa.Column(
            "recommended_fee_kobo", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "club_plan_versions",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "club_plan_versions",
        sa.Column("source_plan_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        "UPDATE club_plan_versions SET published_at = created_at, recommended_fee_kobo = club_fee_kobo"
    )
    op.drop_constraint(
        "ck_club_plan_sessions_positive", "club_plan_versions", type_="check"
    )
    op.drop_constraint(
        "ck_club_plan_minimum_entry_sessions", "club_plan_versions", type_="check"
    )
    op.create_check_constraint(
        "ck_club_plan_sessions_positive", "club_plan_versions", "sessions_included >= 0"
    )
    op.create_check_constraint(
        "ck_club_plan_minimum_entry_sessions",
        "club_plan_versions",
        "minimum_entry_sessions > 0 AND (NOT is_active OR minimum_entry_sessions <= sessions_included)",
    )
    op.create_table(
        "club_plan_sessions",
        pk(),
        ref("plan_version_id", "club_plan_versions.id", "CASCADE"),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("pool_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("fee_kobo", sa.Integer(), nullable=False),
        sa.Column("pricing_snapshot", pg.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "plan_version_id", "session_id", name="uq_club_plan_session"
        ),
        sa.CheckConstraint("fee_kobo >= 0", name="ck_club_plan_session_fee"),
    )
    op.create_index(
        "ix_club_plan_sessions_plan_version_id",
        "club_plan_sessions",
        ["plan_version_id"],
    )
    op.create_index(
        "ix_club_plan_sessions_session_id", "club_plan_sessions", ["session_id"]
    )
    for name in ("member_guest_fee_kobo", "public_guest_fee_kobo", "capacity"):
        op.add_column(
            "community_experience_offerings",
            sa.Column(name, sa.Integer(), nullable=True),
        )
    op.add_column(
        "community_experience_offerings",
        sa.Column(
            "max_guests_per_member", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_check_constraint(
        "ck_experience_guest_capacity",
        "community_experience_offerings",
        "max_guests_per_member >= 0 AND (capacity IS NULL OR capacity > 0)",
    )
    op.create_check_constraint(
        "ck_experience_guest_fees",
        "community_experience_offerings",
        "(member_guest_fee_kobo IS NULL OR member_guest_fee_kobo >= 0) AND (public_guest_fee_kobo IS NULL OR public_guest_fee_kobo >= 0)",
    )
    op.create_table(
        "community_experience_events",
        pk(),
        ref("offering_id", "community_experience_offerings.id", "CASCADE"),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("club_impact", sa.String(16), nullable=False),
        sa.Column("replaced_session_ids", pg.JSONB(), nullable=False),
        sa.Column("event_snapshot", pg.JSONB(), nullable=False),
        sa.CheckConstraint(
            "club_impact IN ('separate','parallel','replaces')",
            name="ck_experience_club_impact",
        ),
    )
    op.create_index(
        "ix_community_experience_events_offering_id",
        "community_experience_events",
        ["offering_id"],
    )
    op.create_table(
        "community_experience_orders",
        pk(),
        ref("offering_id", "community_experience_offerings.id"),
        ref("member_id", "members.id", nullable=True),
        sa.Column("member_auth_id", sa.String(), nullable=True),
        sa.Column("payer_email", sa.String(), nullable=False),
        sa.Column("access_token_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payment_reference", sa.String(128), nullable=False, unique=True),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("amount_kobo", sa.Integer(), nullable=False),
        sa.Column("checkout_url", sa.String(), nullable=True),
        sa.Column("membership_fee_kobo", sa.Integer(), nullable=False),
        sa.Column("membership_months", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_kobo >= 0 AND membership_fee_kobo >= 0",
            name="ck_experience_order_amount",
        ),
    )
    op.create_index(
        "ix_community_experience_orders_offering_id",
        "community_experience_orders",
        ["offering_id"],
    )
    op.create_index(
        "ix_community_experience_orders_member_id",
        "community_experience_orders",
        ["member_id"],
    )
    op.create_table(
        "community_experience_participants",
        pk(),
        ref("order_id", "community_experience_orders.id", "CASCADE"),
        sa.Column("member_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("ticket_kind", sa.String(32), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("phone", sa.String(40), nullable=False),
        sa.Column("emergency_contact", pg.JSONB(), nullable=False),
        sa.Column("waiver_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_kobo", sa.Integer(), nullable=False),
        sa.CheckConstraint("price_kobo >= 0", name="ck_experience_participant_price"),
    )
    op.create_index(
        "ix_community_experience_participants_order_id",
        "community_experience_participants",
        ["order_id"],
    )
    op.create_table(
        "community_experience_attendance",
        pk(),
        ref("participant_id", "community_experience_participants.id", "CASCADE"),
        sa.Column("event_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("checked_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_in_by", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "participant_id", "event_id", name="uq_experience_event_attendance"
        ),
    )


def downgrade():
    # A downgrade cannot safely discard paid participant history or zero-session
    # drafts. Refuse it once the new structures contain operational records.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM community_experience_orders) OR EXISTS (SELECT 1 FROM club_plan_versions WHERE sessions_included = 0) THEN RAISE EXCEPTION 'Archive/reconcile Experience orders and empty Club drafts before downgrade'; END IF; END $$"
    )
    for table in (
        "community_experience_attendance",
        "community_experience_participants",
        "community_experience_orders",
        "community_experience_events",
        "club_plan_sessions",
    ):
        op.drop_table(table)
    op.drop_constraint(
        "ck_experience_guest_capacity", "community_experience_offerings", type_="check"
    )
    op.drop_constraint(
        "ck_experience_guest_fees", "community_experience_offerings", type_="check"
    )
    for name in (
        "member_guest_fee_kobo",
        "public_guest_fee_kobo",
        "max_guests_per_member",
        "capacity",
    ):
        op.drop_column("community_experience_offerings", name)
    op.drop_constraint(
        "ck_club_plan_sessions_positive", "club_plan_versions", type_="check"
    )
    op.drop_constraint(
        "ck_club_plan_minimum_entry_sessions", "club_plan_versions", type_="check"
    )
    op.create_check_constraint(
        "ck_club_plan_sessions_positive", "club_plan_versions", "sessions_included > 0"
    )
    op.create_check_constraint(
        "ck_club_plan_minimum_entry_sessions",
        "club_plan_versions",
        "minimum_entry_sessions > 0 AND minimum_entry_sessions <= sessions_included",
    )
    for name in ("recommended_fee_kobo", "published_at", "source_plan_id"):
        op.drop_column("club_plan_versions", name)
