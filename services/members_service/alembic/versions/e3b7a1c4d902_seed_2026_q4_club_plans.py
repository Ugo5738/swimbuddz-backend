"""seed purchasable 2026 Q4 plans for configured Club locations

Revision ID: e3b7a1c4d902
Revises: c7d5e9f3b842
Create Date: 2026-09-06

Production already has active Club and pool records, but no plan versions.
That leaves the member plan picker empty. This data migration publishes the
existing configured quarterly price only for Clubs that already have a
default pool. The plan keeps any Club-level operating area already configured;
the member picker also supports its explicit "Other Lagos locations" fallback
for legacy Clubs whose area has not yet been assigned.

Existing or admin-created Q4 plans always win: the insert is deliberately
idempotent and never updates a plan that is already present.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e3b7a1c4d902"
down_revision: Union[str, None] = "c7d5e9f3b842"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO club_plan_versions (
            id,
            club_id,
            pool_id,
            operating_area_id,
            name,
            billing_cycle,
            currency,
            club_fee_kobo,
            community_experience_fee_kobo,
            community_experience_default_selected,
            community_experience_offering_id,
            sessions_included,
            period_start,
            period_end,
            minimum_entry_sessions,
            refreshments_included,
            capacity,
            premium_venue_note,
            effective_from,
            effective_to,
            is_active,
            created_at,
            updated_at
        )
        SELECT
            md5('swimbuddz:club-plan:2026-q4:' || club.id::text)::uuid,
            club.id,
            club.default_pool_id,
            club.operating_area_id,
            left('2026 Q4 Club - ' || club.name, 160),
            'quarterly',
            'NGN',
            4250000,
            3000000,
            true,
            NULL,
            12,
            DATE '2026-10-01',
            DATE '2026-12-31',
            5,
            true,
            NULL,
            NULL,
            DATE '2026-09-01',
            DATE '2026-12-31',
            true,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM clubs AS club
        WHERE club.is_active IS TRUE
          AND club.default_pool_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM club_plan_versions AS existing
              WHERE existing.club_id = club.id
                AND existing.period_start = DATE '2026-10-01'
                AND existing.period_end = DATE '2026-12-31'
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM club_plan_versions AS plan
        USING clubs AS club
        WHERE plan.club_id = club.id
          AND plan.id = md5(
              'swimbuddz:club-plan:2026-q4:' || club.id::text
          )::uuid
          AND plan.period_start = DATE '2026-10-01'
          AND plan.period_end = DATE '2026-12-31'
        """
    )
