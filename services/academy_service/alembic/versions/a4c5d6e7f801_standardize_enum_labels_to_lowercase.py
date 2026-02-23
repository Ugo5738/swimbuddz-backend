"""standardize_enum_labels_to_lowercase

Revision ID: a4c5d6e7f801
Revises: 479a12a2b963
Create Date: 2026-02-22 05:40:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a4c5d6e7f801"
down_revision = "479a12a2b963"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "program_level_enum": [
        ("BEGINNER_1", "beginner_1"),
        ("BEGINNER_2", "beginner_2"),
        ("INTERMEDIATE", "intermediate"),
        ("ADVANCED", "advanced"),
        ("SPECIALTY", "specialty"),
    ],
    "billing_type_enum": [
        ("ONE_TIME", "one_time"),
        ("SUBSCRIPTION", "subscription"),
        ("PER_SESSION", "per_session"),
    ],
    "location_type_enum": [
        ("POOL", "pool"),
        ("OPEN_WATER", "open_water"),
        ("REMOTE", "remote"),
    ],
    "cohort_status_enum": [
        ("OPEN", "open"),
        ("ACTIVE", "active"),
        ("COMPLETED", "completed"),
        ("CANCELLED", "cancelled"),
    ],
    "resource_source_type_enum": [("URL", "url"), ("UPLOAD", "upload")],
    "resource_visibility_enum": [
        ("PUBLIC", "public"),
        ("ENROLLED_ONLY", "enrolled_only"),
        ("COACHES_ONLY", "coaches_only"),
    ],
    "enrollment_status_enum": [
        ("PENDING_APPROVAL", "pending_approval"),
        ("ENROLLED", "enrolled"),
        ("WAITLIST", "waitlist"),
        ("DROPOUT_PENDING", "dropout_pending"),
        ("DROPPED", "dropped"),
        ("GRADUATED", "graduated"),
    ],
    "academy_payment_status_enum": [
        ("PENDING", "pending"),
        ("PAID", "paid"),
        ("FAILED", "failed"),
        ("WAIVED", "waived"),
    ],
    "enrollment_source_enum": [
        ("WEB", "web"),
        ("ADMIN", "admin"),
        ("PARTNER", "partner"),
    ],
    "installment_status_enum": [
        ("PENDING", "pending"),
        ("PAID", "paid"),
        ("MISSED", "missed"),
        ("WAIVED", "waived"),
    ],
    "milestone_type_enum": [
        ("SKILL", "skill"),
        ("ENDURANCE", "endurance"),
        ("TECHNIQUE", "technique"),
        ("ASSESSMENT", "assessment"),
    ],
    "required_evidence_enum": [
        ("NONE", "none"),
        ("VIDEO", "video"),
        ("TIME_TRIAL", "time_trial"),
    ],
    "progress_status_enum": [("PENDING", "pending"), ("ACHIEVED", "achieved")],
}

COLUMN_ENUM_MAP = [
    ("programs", "level", "program_level_enum"),
    ("programs", "billing_type", "billing_type_enum"),
    ("cohorts", "location_type", "location_type_enum"),
    ("cohorts", "status", "cohort_status_enum"),
    ("cohort_resources", "source_type", "resource_source_type_enum"),
    ("cohort_resources", "visibility", "resource_visibility_enum"),
    ("enrollments", "status", "enrollment_status_enum"),
    ("enrollments", "payment_status", "academy_payment_status_enum"),
    ("enrollments", "source", "enrollment_source_enum"),
    ("enrollment_installments", "status", "installment_status_enum"),
    ("milestones", "milestone_type", "milestone_type_enum"),
    ("milestones", "required_evidence", "required_evidence_enum"),
    ("student_progress", "status", "progress_status_enum"),
]


def upgrade() -> None:
    # Introduce lowercase enum labels in autocommit mode so they can be used below.
    with op.get_context().autocommit_block():
        for enum_type, pairs in ENUM_VALUE_MAP.items():
            for _, new in pairs:
                op.execute(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{new}'")

    # Backfill rows from legacy uppercase labels to lowercase labels.
    for table_name, column_name, enum_type in COLUMN_ENUM_MAP:
        for old, new in ENUM_VALUE_MAP[enum_type]:
            op.execute(
                f"UPDATE {table_name} SET {column_name} = '{new}' WHERE {column_name}::text = '{old}'"
            )

    # Align DB defaults with lowercase labels used by model .value persistence.
    op.execute("ALTER TABLE programs ALTER COLUMN billing_type SET DEFAULT 'one_time'")
    op.execute("ALTER TABLE cohorts ALTER COLUMN location_type SET DEFAULT 'pool'")
    op.execute("ALTER TABLE cohort_resources ALTER COLUMN source_type SET DEFAULT 'url'")
    op.execute(
        "ALTER TABLE cohort_resources ALTER COLUMN visibility SET DEFAULT 'enrolled_only'"
    )
    op.execute("ALTER TABLE enrollments ALTER COLUMN source SET DEFAULT 'web'")
    op.execute(
        "ALTER TABLE enrollment_installments ALTER COLUMN status SET DEFAULT 'pending'"
    )
    op.execute("ALTER TABLE milestones ALTER COLUMN milestone_type SET DEFAULT 'skill'")
    op.execute("ALTER TABLE milestones ALTER COLUMN required_evidence SET DEFAULT 'none'")


def downgrade() -> None:
    # Intentionally no-op: enum label removal is destructive.
    pass
