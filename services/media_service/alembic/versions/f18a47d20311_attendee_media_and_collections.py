"""Attendance media access and multiple member collections.

Revision ID: f18a47d20311
Revises: e16a4e8b2970
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "f18a47d20311"
down_revision: Union[str, None] = "e16a4e8b2970"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_media_vaults_owner_member_id", "media_vaults", type_="unique"
    )
    op.create_index(
        "uq_media_vaults_primary_member_upload",
        "media_vaults",
        ["owner_member_id"],
        unique=True,
        postgresql_where=sa.text(
            "owner_member_id IS NOT NULL AND settings_json ->> 'member_self_upload' = 'true'"
        ),
    )
    op.add_column(
        "media_vaults",
        sa.Column(
            "attendee_can_view", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "media_vaults",
        sa.Column(
            "attendee_can_contribute",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_table(
        "media_vault_attendance_denials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vault_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_vaults.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("vault_id", "member_id", name="uq_vault_attendance_deny"),
    )
    op.create_index(
        "ix_media_vault_attendance_denials_vault_id",
        "media_vault_attendance_denials",
        ["vault_id"],
    )
    op.create_index(
        "ix_media_vault_attendance_denials_member_id",
        "media_vault_attendance_denials",
        ["member_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_vault_attendance_denials_member_id",
        table_name="media_vault_attendance_denials",
    )
    op.drop_index(
        "ix_media_vault_attendance_denials_vault_id",
        table_name="media_vault_attendance_denials",
    )
    op.drop_table("media_vault_attendance_denials")
    op.drop_column("media_vaults", "attendee_can_contribute")
    op.drop_column("media_vaults", "attendee_can_view")
    op.drop_index("uq_media_vaults_primary_member_upload", table_name="media_vaults")
    op.create_unique_constraint(
        "uq_media_vaults_owner_member_id", "media_vaults", ["owner_member_id"]
    )
