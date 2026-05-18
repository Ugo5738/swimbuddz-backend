"""lowercase_stray_uppercase_enrollment_status_rows

Revision ID: 9b6e8f7499d8
Revises: a672e7f12a65
Create Date: 2026-05-18 09:36:35.404776

Hand-written data migration. Generated via
`./scripts/db/migrate.sh --manual` so the revision id is
Alembic-assigned and the chain stays intact; the body is a targeted
data normalization Alembic autogenerate cannot represent.

Context — `a4c5d6e7f801_standardize_enum_labels_to_lowercase`
(2026-02) lowercased every enrollment status row and added the
lowercase enum labels. A read-only prod audit found ONE
`enrollments` row still at the uppercase ``'ENROLLED'`` label (vs 10
correct ``'enrolled'``). It postdates that migration's UPDATE, so it
was written during a transitional window by code that has since been
corrected (current code uses ``EnrollmentStatus.ENROLLED == 'enrolled'``
exclusively — no hardcoded uppercase remains). The stray row makes
SQLAlchemy raise ``LookupError: 'ENROLLED' is not among the defined
enum values`` while *reading* it, 500-ing
``GET /internal/academy/cohorts/{id}/enrollment-counts`` (and the
cohort enrollment displays it feeds).

Fix: lowercase any `enrollments.status` whose label is not already
lowercase. Every uppercase label's lowercased form is a valid existing
member of ``enrollment_status_enum`` (the Feb migration added them), so
the cast is safe. Idempotent — a strict no-op once all rows are
lowercase (dev/staging get a fresh ``reset.sh`` and never hit this).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "9b6e8f7499d8"
down_revision = "a672e7f12a65"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE enrollments "
        "SET status = lower(status::text)::enrollment_status_enum "
        "WHERE status::text <> lower(status::text)"
    )


def downgrade() -> None:
    # No-op: re-introducing the uppercase label would just restore the
    # bug. The lowercase value is the single correct representation.
    pass
