"""protect_externally_payable_club_reservations

Hand-written migration — CHECK constraint expansion is not detected by
Alembic autogenerate. Existing reservation rows and financial data are unchanged.

Revision ID: 1cbbd94c744f
Revises: e9f1a3b5c726
Create Date: 2026-09-28 17:37:41.206159
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1cbbd94c744f"
down_revision = "e9f1a3b5c726"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_club_enrollment_reservation_status",
        "club_enrollment_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_club_enrollment_reservation_status",
        "club_enrollment_reservations",
        "status IN ('active', 'protected', 'consumed', 'released')",
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM club_enrollment_reservations WHERE status = 'protected')"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Reconcile protected Club reservations before downgrading")
    op.drop_constraint(
        "ck_club_enrollment_reservation_status",
        "club_enrollment_reservations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_club_enrollment_reservation_status",
        "club_enrollment_reservations",
        "status IN ('active', 'consumed', 'released')",
    )
