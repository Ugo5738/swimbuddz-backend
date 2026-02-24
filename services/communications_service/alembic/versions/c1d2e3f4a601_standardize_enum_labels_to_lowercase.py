"""standardize_enum_labels_to_lowercase

Revision ID: c1d2e3f4a601
Revises: 995988fb8afb
Create Date: 2026-02-22 05:42:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c1d2e3f4a601"
down_revision = "995988fb8afb"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "announcement_category_enum": [
        ("RAIN_UPDATE", "rain_update"),
        ("SCHEDULE_CHANGE", "schedule_change"),
        ("ACADEMY_UPDATE", "academy_update"),
        ("EVENT", "event"),
        ("COMPETITION", "competition"),
        ("GENERAL", "general"),
        ("CUSTOM", "custom"),
    ],
    "announcement_status_enum": [
        ("DRAFT", "draft"),
        ("PUBLISHED", "published"),
        ("ARCHIVED", "archived"),
    ],
    "announcement_audience_enum": [
        ("COMMUNITY", "community"),
        ("CLUB", "club"),
        ("ACADEMY", "academy"),
    ],
    "message_recipient_type_enum": [("COHORT", "cohort"), ("STUDENT", "student")],
    "session_notification_type_enum": [
        ("SESSION_PUBLISHED", "session_published"),
        ("REMINDER_24H", "reminder_24h"),
        ("REMINDER_3H", "reminder_3h"),
        ("REMINDER_1H", "reminder_1h"),
        ("SESSION_CANCELLED", "session_cancelled"),
        ("SESSION_UPDATED", "session_updated"),
        ("SPOTS_AVAILABLE", "spots_available"),
    ],
    "scheduled_notification_status_enum": [
        ("PENDING", "pending"),
        ("SENT", "sent"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
    ],
}

COLUMN_ENUM_MAP = [
    ("announcements", "category", "announcement_category_enum"),
    ("announcements", "status", "announcement_status_enum"),
    ("announcements", "audience", "announcement_audience_enum"),
    ("message_logs", "recipient_type", "message_recipient_type_enum"),
    ("scheduled_notifications", "notification_type", "session_notification_type_enum"),
    ("scheduled_notifications", "status", "scheduled_notification_status_enum"),
    ("session_notification_logs", "notification_type", "session_notification_type_enum"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for enum_type, pairs in ENUM_VALUE_MAP.items():
            for _, new in pairs:
                op.execute(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{new}'")

    for table_name, column_name, enum_type in COLUMN_ENUM_MAP:
        for old, new in ENUM_VALUE_MAP[enum_type]:
            op.execute(
                f"UPDATE {table_name} SET {column_name} = '{new}' WHERE {column_name}::text = '{old}'"
            )


def downgrade() -> None:
    # Intentionally no-op: enum label removal is destructive.
    pass
