"""drop_uppercase_enum_labels_data_already_lowercase

Hand-written migration — Postgres has no ``ALTER TYPE … DROP VALUE``,
so removing the legacy uppercase labels requires the
rename-recreate-cast-drop dance for each enum type. Alembic
autogenerate cannot represent this.

**Context.** Migration ``a4c5d6e7f801`` ("standardize_enum_labels_to_lowercase",
2026-02-22) added lowercase labels to 13 academy enums and backfilled
all then-existing data. The original uppercase labels were left in
place so any in-flight writes from older code wouldn't break.

The transition is now complete:

* All Python ``str.Enum`` classes in ``services/academy_service/models/enums.py``
  use lowercase ``.value``\\ s exclusively, and SAEnum columns use
  ``values_callable=enum_values`` to persist the lowercase value.
* As of 2026-05-20, a thorough audit (every enum column in every
  table) confirmed no row anywhere stores an uppercase value.
* One stray uppercase enrollment + its installments from a 2026-05-14
  retroactive backfill (likely a script that pre-dated normalisation)
  was repaired in-place.

Leaving the uppercase labels around lets the same bug re-emerge if
*any* future write path produces uppercase strings (a stale script, a
hand-typed seed, a copy-pasted migration). Dropping the labels closes
that door — any future uppercase write fails at INSERT time with a
clear "invalid input value for enum" rather than corrupting silently
and surfacing later as a SQLAlchemy ``LookupError`` on read.

**Safety.** Before recreating each enum, the migration asserts the
target column contains no remaining uppercase data. If anything is
found, the migration aborts cleanly with a Postgres ``RAISE
EXCEPTION``. This avoids the failure mode where the recreate proceeds
and a ``USING col::text::new_enum`` cast crashes on the offending
row, leaving the type half-dropped.

**Defaults.** Column defaults are captured into a temporary table at
the start of ``upgrade()`` and re-applied after each column type
change, so we don't have to hardcode 13 column defaults here (and so
the migration stays correct if a default ever changes).

**Downgrade.** Intentionally a no-op. Re-introducing uppercase labels
on a clean enum would silently allow new uppercase writes — the
exact thing this migration is designed to prevent. If a future
contributor genuinely needs to roll back, do it by hand with an
``ALTER TYPE … ADD VALUE`` for each label.

Revision ID: e946432f8fb7
Revises: ba3150f4c374
Create Date: 2026-05-20 14:10:21.259469
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e946432f8fb7"
down_revision = "ba3150f4c374"
branch_labels = None
depends_on = None


# Lowercase value lists for each enum, derived from the Python
# ``str.Enum`` classes in ``services/academy_service/models/enums.py``.
# Keep this list in sync if a label is ever added.
ENUM_LOWERCASE_VALUES: dict[str, list[str]] = {
    "program_level_enum": [
        "beginner_1",
        "beginner_2",
        "intermediate",
        "advanced",
        "specialty",
    ],
    "billing_type_enum": ["one_time", "subscription", "per_session"],
    "location_type_enum": ["pool", "open_water", "remote"],
    "cohort_status_enum": ["open", "active", "completed", "cancelled"],
    "resource_source_type_enum": ["url", "upload"],
    "resource_visibility_enum": ["public", "enrolled_only", "coaches_only"],
    "enrollment_status_enum": [
        "pending_approval",
        "enrolled",
        "waitlist",
        "dropout_pending",
        "dropped",
        "graduated",
    ],
    "academy_payment_status_enum": ["pending", "paid", "failed", "waived"],
    "enrollment_source_enum": ["web", "admin", "partner"],
    "installment_status_enum": ["pending", "paid", "missed", "waived"],
    "milestone_type_enum": ["skill", "endurance", "technique", "assessment"],
    "required_evidence_enum": ["none", "video", "time_trial"],
    "progress_status_enum": ["pending", "achieved"],
}

# Ordering follows migration ``a4c5d6e7f801`` for the first 13 rows,
# then adds the two ``milestone_review_events`` columns that also
# reference ``progress_status_enum`` — that older migration's
# ``COLUMN_ENUM_MAP`` predated the audit-log table and didn't list
# them. They MUST be included here, otherwise the ``DROP TYPE`` for
# the legacy ``progress_status_enum`` will fail with
# ``DependentObjectsStillExist``. ``milestone_event_type_enum`` is
# deliberately not listed: it was created lowercase-only from the
# start (its values ``claimed/approved/rejected/status_changed``
# never had uppercase variants), so it has nothing to drop.
COLUMN_ENUM_MAP: list[tuple[str, str, str]] = [
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
    ("milestone_review_events", "previous_status", "progress_status_enum"),
    ("milestone_review_events", "new_status", "progress_status_enum"),
]


def upgrade() -> None:
    # ── 1. Safety assertion: no row anywhere may still hold an uppercase value ──
    # If anything is found, abort with a clear message naming the table.column
    # so the operator knows where to look.
    for table, column, _enum in COLUMN_ENUM_MAP:
        op.execute(
            f"""
            DO $$
            DECLARE bad_count INT;
            BEGIN
                EXECUTE format(
                    'SELECT count(*) FROM %I WHERE %I::text ~ ''[A-Z]''',
                    '{table}', '{column}'
                ) INTO bad_count;
                IF bad_count > 0 THEN
                    RAISE EXCEPTION
                        'Refusing to drop uppercase enum labels: '
                        '% rows in {table}.{column} still hold an uppercase value. '
                        'Backfill them to lowercase before applying this migration.',
                        bad_count;
                END IF;
            END $$;
            """
        )

    # ── 2. Capture current column defaults into a per-migration temp table ──
    # We restore these after the type swap. Using a temp table keeps the
    # migration self-contained and immune to default drift.
    op.execute(
        """
        CREATE TEMP TABLE _enum_drop_uppercase_defaults (
            table_name  TEXT NOT NULL,
            column_name TEXT NOT NULL,
            default_expr TEXT,
            PRIMARY KEY (table_name, column_name)
        ) ON COMMIT DROP
        """
    )
    for table, column, _enum in COLUMN_ENUM_MAP:
        op.execute(
            f"""
            INSERT INTO _enum_drop_uppercase_defaults (table_name, column_name, default_expr)
            SELECT '{table}', '{column}', pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE c.relname = '{table}' AND a.attname = '{column}'
            """
        )

    # ── 3. For each enum: rename old, create lowercase-only new, ──
    #     alter every column to use it, then drop the old type.
    for enum_type, values in ENUM_LOWERCASE_VALUES.items():
        legacy = f"{enum_type}__legacy_uppercase"
        new_values_sql = ", ".join(f"'{v}'" for v in values)

        op.execute(f"ALTER TYPE {enum_type} RENAME TO {legacy}")
        op.execute(f"CREATE TYPE {enum_type} AS ENUM ({new_values_sql})")

        for t, c, e in COLUMN_ENUM_MAP:
            if e != enum_type:
                continue
            # Drop the default first — ALTER COLUMN TYPE doesn't carry
            # over defaults whose expression references the now-renamed
            # legacy type.
            op.execute(f"ALTER TABLE {t} ALTER COLUMN {c} DROP DEFAULT")
            op.execute(
                f"ALTER TABLE {t} ALTER COLUMN {c} TYPE {enum_type} "
                f"USING {c}::text::{enum_type}"
            )

        op.execute(f"DROP TYPE {legacy}")

    # ── 4. Restore captured defaults (now expressed against the new enum types) ──
    op.execute(
        """
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN SELECT * FROM _enum_drop_uppercase_defaults
                     WHERE default_expr IS NOT NULL LOOP
                EXECUTE format(
                    'ALTER TABLE %I ALTER COLUMN %I SET DEFAULT %s',
                    r.table_name, r.column_name, r.default_expr
                );
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # Deliberately a no-op. See the migration docstring for why
    # re-introducing uppercase labels would defeat the purpose.
    pass
