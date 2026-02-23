"""standardize_enum_labels_to_lowercase

Revision ID: f1a2b3c4d901
Revises: 91af1bd9e724
Create Date: 2026-02-22 05:45:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d901"
down_revision = "91af1bd9e724"
branch_labels = None
depends_on = None


ENUM_VALUE_MAP = {
    "volunteer_role_category": [
        ("SESSION_LEAD", "session_lead"),
        ("WARMUP_LEAD", "warmup_lead"),
        ("LANE_MARSHAL", "lane_marshal"),
        ("CHECKIN", "checkin"),
        ("SAFETY", "safety"),
        ("WELCOME", "welcome"),
        ("RIDE_SHARE", "ride_share"),
        ("MENTOR", "mentor"),
        ("MEDIA", "media"),
        ("GALLERY_SUPPORT", "gallery_support"),
        ("EVENTS_LOGISTICS", "events_logistics"),
        ("TRIP_PLANNER", "trip_planner"),
        ("ACADEMY_ASSISTANT", "academy_assistant"),
        ("OTHER", "other"),
    ],
    "volunteer_tier": [
        ("TIER_1", "tier_1"),
        ("TIER_2", "tier_2"),
        ("TIER_3", "tier_3"),
    ],
    "recognition_tier": [
        ("BRONZE", "bronze"),
        ("SILVER", "silver"),
        ("GOLD", "gold"),
    ],
    "opportunity_type": [
        ("OPEN_CLAIM", "open_claim"),
        ("APPROVAL_REQUIRED", "approval_required"),
    ],
    "opportunity_status": [
        ("DRAFT", "draft"),
        ("OPEN", "open"),
        ("FILLED", "filled"),
        ("IN_PROGRESS", "in_progress"),
        ("COMPLETED", "completed"),
        ("CANCELLED", "cancelled"),
    ],
    "slot_status": [
        ("CLAIMED", "claimed"),
        ("APPROVED", "approved"),
        ("REJECTED", "rejected"),
        ("CANCELLED", "cancelled"),
        ("NO_SHOW", "no_show"),
        ("COMPLETED", "completed"),
    ],
    "reward_type": [
        ("DISCOUNTED_SESSION", "discounted_session"),
        ("FREE_MERCH", "free_merch"),
        ("PRIORITY_EVENT", "priority_event"),
        ("MEMBERSHIP_DISCOUNT", "membership_discount"),
        ("CUSTOM", "custom"),
    ],
}

COLUMN_ENUM_MAP = [
    ("volunteer_roles", "category", "volunteer_role_category"),
    ("volunteer_roles", "min_tier", "volunteer_tier"),
    ("volunteer_profiles", "tier", "volunteer_tier"),
    ("volunteer_profiles", "tier_override", "volunteer_tier"),
    ("volunteer_profiles", "recognition_tier", "recognition_tier"),
    ("volunteer_opportunities", "opportunity_type", "opportunity_type"),
    ("volunteer_opportunities", "status", "opportunity_status"),
    ("volunteer_opportunities", "min_tier", "volunteer_tier"),
    ("volunteer_slots", "status", "slot_status"),
    ("volunteer_rewards", "reward_type", "reward_type"),
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
