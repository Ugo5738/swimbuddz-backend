"""B4 audit canonical shape: stage 2 — backfill, NOT NULL, drop legacy.

Hand-written migration — autogenerate cannot represent the data
backfill needed before flipping NOT NULL on the new columns. Stage 1
(``7ff40e02772d_b4_audit_canonical_shape_add_new_columns.py``) added
the canonical columns as nullable; this stage populates them from the
existing rows, then converts ``entity_type`` from enum→String, ALTERs
the populated columns to NOT NULL, drops the legacy
``performed_at`` / ``performed_by`` / ``notes`` columns, and drops the
old enum type + old indexes.

Ordering is deliberate:

1. **Convert action enum→String FIRST** (no enum exists today on
   ``action`` — it's already VARCHAR(50), so this is a width bump).
   ``entity_type`` IS an enum (``store_audit_entity_type_enum``);
   convert it BEFORE the backfill UPDATE so the UPDATE doesn't try to
   write an enum value to a still-enum column with a different cast.
2. **Backfill** the new columns from the old ones in a single UPDATE:

   - ``domain`` = constant ``'store'``
   - ``actor_label`` = ``performed_by`` (always preserved)
   - ``actor_id`` = ``performed_by::uuid`` when it matches UUID regex,
     else NULL (matches Python-side ``parse_uuid_or_none``)
   - ``reason`` = ``notes``
   - ``created_at`` = ``performed_at``
   - ``action`` = ``'store.' || action`` (namespace per canonical shape)
3. **Verify row-count invariant** — RAISE if any row failed to backfill
   ``domain`` / ``actor_label`` / ``created_at`` (the always-present
   fields). Audit data is compliance-relevant; no silent loss.
4. **ALTER COLUMN ... NOT NULL** on backfilled columns.
5. **Drop legacy** columns (``performed_at`` / ``performed_by`` /
   ``notes``) and the enum type (``store_audit_entity_type_enum``)
   and the old indexes (``ix_store_audit_logs_entity`` and
   ``ix_store_audit_logs_performed_at``).

Downgrade reverses by restoring the legacy columns from the canonical
ones (lossy on enum coercion if the entity_type string isn't one of the
8 known values — but downgrade is for emergencies, not routine ops).
"""

revision = "2de3f310326d"
down_revision = '7ff40e02772d'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


# Stable enum values for the legacy entity_type enum. Recreating these
# in downgrade allows us to coerce string entity_type values BACK to
# the enum — but only for rows where the value is one of these 8.
LEGACY_ENTITY_TYPES = (
    'product',
    'inventory',
    'order',
    'store_credit',
    'category',
    'pickup_location',
    'supplier',
    'supplier_payout',
)

UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    conn = op.get_bind()

    # ── Pre-flight: capture pre-backfill row count for invariant check ─
    pre_count = conn.execute(
        sa.text("SELECT count(*) FROM store_audit_logs")
    ).scalar()

    # ── Convert entity_type enum → String(64) BEFORE backfill UPDATE ──
    # The new column is sa.String(64). If we leave it as enum during
    # the UPDATE, PostgreSQL may complain about cast mismatches when
    # we later write namespaced action strings into it. Doing this now
    # also makes the UPDATE simpler (no per-row enum casting).
    op.execute(
        "ALTER TABLE store_audit_logs "
        "ALTER COLUMN entity_type TYPE varchar(64) "
        "USING entity_type::text"
    )

    # ── Widen action from VARCHAR(50) → String(128) ───────────────────
    # Required to fit namespaced "store.<verb>" values within the
    # canonical 128-char ceiling (mixin) without truncating existing
    # 50-char actions.
    op.execute(
        "ALTER TABLE store_audit_logs "
        "ALTER COLUMN action TYPE varchar(128)"
    )

    # ── Backfill ──────────────────────────────────────────────────────
    # Single UPDATE for atomicity inside alembic's per-migration tx.
    # If anything mid-statement fails, the whole migration rolls back.
    op.execute(
        sa.text(
            """
            UPDATE store_audit_logs
            SET domain = 'store',
                actor_label = performed_by,
                actor_id = CASE
                    WHEN performed_by ~ :uuid_re
                    THEN performed_by::uuid
                    ELSE NULL
                END,
                reason = notes,
                created_at = performed_at,
                action = 'store.' || action
            """
        ).bindparams(uuid_re=UUID_RE)
    )

    # ── Verify row-count invariant ────────────────────────────────────
    # Every existing row should now have non-null domain / actor_label
    # / created_at. (actor_id is intentionally allowed NULL — that's
    # the "no parseable UUID" branch.)
    bad = conn.execute(
        sa.text(
            "SELECT count(*) FROM store_audit_logs "
            "WHERE domain IS NULL "
            "OR actor_label IS NULL "
            "OR created_at IS NULL"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"B4 store backfill row-count invariant failed: {bad} of "
            f"{pre_count} rows still have NULL canonical columns "
            f"(domain / actor_label / created_at). Refusing to ALTER "
            f"NOT NULL."
        )

    # ── ALTER NOT NULL on backfilled canonical columns ────────────────
    op.alter_column(
        'store_audit_logs', 'domain',
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        'store_audit_logs', 'created_at',
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    # actor_label is canonical-nullable (the mixin lets actor_label be
    # NULL for system-actor writes). We don't ALTER it NOT NULL even
    # though store rows always had `performed_by` populated — keeping
    # it nullable matches the mixin and future writers (e.g. system
    # cron actions) won't need a schema migration.

    # ── Drop legacy columns + old indexes + old enum type ─────────────
    op.drop_index('ix_store_audit_logs_entity', table_name='store_audit_logs')
    op.drop_index('ix_store_audit_logs_performed_at', table_name='store_audit_logs')
    op.drop_column('store_audit_logs', 'performed_at')
    op.drop_column('store_audit_logs', 'performed_by')
    op.drop_column('store_audit_logs', 'notes')
    op.execute("DROP TYPE IF EXISTS store_audit_entity_type_enum")


def downgrade() -> None:
    # ── Recreate legacy enum type + columns ───────────────────────────
    legacy_values_sql = ", ".join(f"'{v}'" for v in LEGACY_ENTITY_TYPES)
    op.execute(
        f"CREATE TYPE store_audit_entity_type_enum AS ENUM ({legacy_values_sql})"
    )

    op.add_column(
        'store_audit_logs',
        sa.Column('notes', sa.Text(), nullable=True),
    )
    op.add_column(
        'store_audit_logs',
        sa.Column('performed_by', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'store_audit_logs',
        sa.Column(
            'performed_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # ── Reverse backfill (lossy on action if any verb didn't start
    # with 'store.', and lossy on entity_type if the string isn't one
    # of the 8 enum values). ─────────────────────────────────────────
    op.execute(
        """
        UPDATE store_audit_logs
        SET notes = reason,
            performed_by = COALESCE(actor_label, actor_id::text),
            performed_at = created_at,
            action = CASE
                WHEN action LIKE 'store.%' THEN substring(action FROM 7)
                ELSE action
            END
        """
    )

    # performed_by was NOT NULL originally; require it now too.
    op.alter_column(
        'store_audit_logs', 'performed_by',
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.alter_column(
        'store_audit_logs', 'performed_at',
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    # Coerce entity_type back to enum (rows with unknown values fail
    # here — that's the downgrade lossiness, which is acceptable for an
    # emergency-only path).
    op.execute(
        "ALTER TABLE store_audit_logs "
        "ALTER COLUMN entity_type TYPE store_audit_entity_type_enum "
        "USING entity_type::store_audit_entity_type_enum"
    )

    # Narrow action back to VARCHAR(50). Will fail if any row exceeds
    # 50 chars after the namespace strip — acceptable downgrade
    # constraint.
    op.execute(
        "ALTER TABLE store_audit_logs "
        "ALTER COLUMN action TYPE varchar(50)"
    )

    # Restore old indexes.
    op.create_index(
        'ix_store_audit_logs_performed_at',
        'store_audit_logs',
        ['performed_at'],
        unique=False,
    )
    op.create_index(
        'ix_store_audit_logs_entity',
        'store_audit_logs',
        ['entity_type', 'entity_id'],
        unique=False,
    )

    # Relax canonical NOT NULLs back to nullable (stage 1 added them
    # as nullable; stage 1's downgrade will drop them entirely).
    op.alter_column(
        'store_audit_logs', 'created_at',
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
    op.alter_column(
        'store_audit_logs', 'domain',
        existing_type=sa.String(length=32),
        nullable=True,
    )
