"""add standalone guest passes and audience-specific session prices

Revision ID: e8c4a2f6b901
Revises: d04e16b7a893
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8c4a2f6b901"
down_revision = "d04e16b7a893"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("guest_fee_kobo", sa.Integer(), nullable=True))
    op.add_column(
        "sessions", sa.Column("community_dropin_fee_kobo", sa.Integer(), nullable=True)
    )
    op.create_table(
        "guest_passes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("guardian_name", sa.String(length=160), nullable=True),
        sa.Column("guardian_phone", sa.String(length=32), nullable=True),
        sa.Column("waiver_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "marketing_consent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("referral_code", sa.String(length=40), nullable=True),
        sa.Column("referrer_auth_id", sa.String(), nullable=True),
        sa.Column("price_kobo", sa.Integer(), nullable=False),
        sa.Column(
            "additional_charges",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("total_kobo", sa.Integer(), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending_payment",
            nullable=False,
        ),
        sa.Column(
            "referral_reward_bubbles", sa.Integer(), server_default="10", nullable=False
        ),
        sa.Column(
            "referral_reward_status",
            sa.String(length=24),
            server_default="not_eligible",
            nullable=False,
        ),
        sa.Column("attended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_swim_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "assessment_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("converted_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("price_kobo >= 0", name="ck_guest_pass_price_nonnegative"),
        sa.CheckConstraint(
            "total_kobo >= price_kobo", name="ck_guest_pass_total_valid"
        ),
        sa.CheckConstraint(
            "actual_swim_minutes IS NULL OR actual_swim_minutes >= 0",
            name="ck_guest_pass_minutes_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_reference"),
        sa.UniqueConstraint("session_id", "phone", name="uq_guest_pass_session_phone"),
    )
    op.create_index("ix_guest_passes_session_id", "guest_passes", ["session_id"])
    op.create_index("ix_guest_passes_email", "guest_passes", ["email"])
    op.create_index("ix_guest_passes_phone", "guest_passes", ["phone"])
    op.create_index("ix_guest_passes_referral_code", "guest_passes", ["referral_code"])
    op.create_index(
        "ix_guest_passes_referrer_auth_id", "guest_passes", ["referrer_auth_id"]
    )
    # Bring existing member-attached guests onto the same identity format as
    # standalone passes so conversion and lifetime swimmer-hours can match.
    op.execute(
        r"""
        UPDATE booking_guests
        SET phone = CASE
            WHEN btrim(phone) LIKE '+%'
                THEN '+' || regexp_replace(phone, '[^0-9]', '', 'g')
            WHEN regexp_replace(phone, '[^0-9]', '', 'g') LIKE '234%'
                THEN '+' || regexp_replace(phone, '[^0-9]', '', 'g')
            WHEN length(regexp_replace(phone, '[^0-9]', '', 'g')) = 11
                 AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE '0%'
                THEN '+234' || substring(regexp_replace(phone, '[^0-9]', '', 'g') FROM 2)
            ELSE regexp_replace(phone, '[^0-9]', '', 'g')
        END
        WHERE phone IS NOT NULL AND btrim(phone) <> ''
        """
    )


def downgrade() -> None:
    op.drop_table("guest_passes")
    op.drop_column("sessions", "community_dropin_fee_kobo")
    op.drop_column("sessions", "guest_fee_kobo")
