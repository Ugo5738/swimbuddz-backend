"""Bound and classify S3 access-log matching attempts without discarding evidence.

Revision ID: a16f4b92c817
Revises: f18a47d20311
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a16f4b92c817"
down_revision: Union[str, None] = "f18a47d20311"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_access_log_events",
        sa.Column(
            "match_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "media_access_log_events",
        sa.Column("last_match_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "media_access_log_events",
        sa.Column("next_match_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Old preview downloads were never included in the original/export ledger.
    # Preserve all rows, AWS request ids, and bytes; classify, never delete.
    op.execute(
        """
        UPDATE media_access_log_events
        SET match_status = 'unattributed', next_match_attempt_at = NULL
        WHERE provider = 's3' AND match_status = 'unmatched'
          AND object_key LIKE 'vault-derivatives/%'
        """
    )
    # Historical events outside the configured seven-day lookback have never
    # had a transfer ledger match. Preserve them as expired audit evidence.
    op.execute(
        """
        UPDATE media_access_log_events
        SET match_status = 'expired', next_match_attempt_at = NULL
        WHERE provider = 's3' AND match_status = 'unmatched'
          AND occurred_at < now() - interval '7 days'
        """
    )
    op.create_index(
        "ix_media_access_events_match_retry_due",
        "media_access_log_events",
        ["next_match_attempt_at", "occurred_at"],
        unique=False,
        postgresql_where=sa.text("provider = 's3' AND match_status = 'unmatched'"),
    )


def downgrade() -> None:
    # A reverse migration must not misrepresent terminal audit statuses as
    # successful matches. Return them to unmatched for legacy reconciliation.
    op.execute(
        """
        UPDATE media_access_log_events
        SET match_status = 'unmatched'
        WHERE match_status IN ('unattributed', 'expired', 'exhausted')
        """
    )
    op.drop_index(
        "ix_media_access_events_match_retry_due",
        table_name="media_access_log_events",
    )
    op.drop_column("media_access_log_events", "next_match_attempt_at")
    op.drop_column("media_access_log_events", "last_match_attempt_at")
    op.drop_column("media_access_log_events", "match_attempts")
