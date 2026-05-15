"""drop_obsolete_singlecolumn_unique_constraint_on_agreement_versions.version

Removes the leftover `agreement_versions_version_key` UNIQUE constraint on
the `version` column alone.

Background:
  * Migration 8e23ec95cf22 (`coach_system_updates`) created the table with
    `sa.UniqueConstraint('version')` — a single-column constraint. Postgres
    auto-named it `agreement_versions_version_key`.
  * Migration 855e579b1d78 (`fix_agreement_versions_agreement_type_…`) added
    the *correct* composite constraint `uq_agreement_version_per_type` on
    `(agreement_type, version)` — but failed to drop the obsolete single-
    column constraint. Both constraints have been live in the DB since.
  * Effect: only ONE row across the whole table could have version="1.0",
    regardless of agreement_type. The coach_agreement seed (v1.0) won;
    the chat_safeguarding seed (also v1.0) failed every reset with
    `duplicate key value violates unique constraint "agreement_versions_
    version_key"`.

The model only declares the composite constraint, so removing the
single-column one aligns the schema with the model. The composite
constraint continues to enforce the correct invariant: each
(agreement_type, version) pair is unique, while different agreement
types can share version strings.

Revision ID: e9b575bf1e25
Revises: ea1abb0ac590
Create Date: 2026-05-15 19:00:45.542233
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "e9b575bf1e25"
down_revision = "ea1abb0ac590"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `IF EXISTS` so reruns / fresh databases (where the original migration
    # may have been collapsed via full-reset) don't fail.
    op.execute(
        'ALTER TABLE agreement_versions '
        'DROP CONSTRAINT IF EXISTS agreement_versions_version_key'
    )


def downgrade() -> None:
    # Restore the obsolete constraint. Only succeeds if no two rows share
    # a version string — would block both seeds running on a downgraded DB.
    op.create_unique_constraint(
        "agreement_versions_version_key",
        "agreement_versions",
        ["version"],
    )
