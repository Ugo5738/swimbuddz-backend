"""add ride passenger manifest

Revision ID: 6c14e28b973a
Revises: be5f99aaf6bc
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "6c14e28b973a"
down_revision = "be5f99aaf6bc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    passenger_type = postgresql.ENUM(
        "member",
        "session_guest",
        "observer",
        name="ride_passenger_type_enum",
        create_type=False,
    )
    passenger_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "ride_passengers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ride_booking_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("passenger_type", passenger_type, nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ride_booking_id"], ["ride_bookings.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ride_passengers_ride_booking_id",
        "ride_passengers",
        ["ride_booking_id"],
    )

    # Existing multi-seat bookings predate manifests. Preserve their occupied
    # seat count with one member and anonymous observers.
    op.execute(
        """
        INSERT INTO ride_passengers
            (id, ride_booking_id, passenger_type, full_name, position, created_at, updated_at)
        SELECT gen_random_uuid(), booking.id,
               CASE WHEN seat.position = 1 THEN 'member'::ride_passenger_type_enum
                    ELSE 'observer'::ride_passenger_type_enum END,
               NULL, seat.position, booking.created_at, booking.updated_at
        FROM ride_bookings AS booking
        CROSS JOIN LATERAL generate_series(1, booking.num_seats) AS seat(position)
        """
    )


def downgrade() -> None:
    op.drop_table("ride_passengers")
    postgresql.ENUM(name="ride_passenger_type_enum").drop(
        op.get_bind(), checkfirst=True
    )
