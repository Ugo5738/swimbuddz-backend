"""B4 audit canonical shape — stage 2: backfill, ALTER NOT NULL, drop old columns.

Stage 2 of 2 for the B4 wallet PR (design:
``docs/design/B4_AUDIT_LOG_UNIFICATION.md``). Stage 1 (f44dfaa2835f)
added the canonical columns as nullable. This stage:

  1. Pre-flight: record the current row count (compliance-grade
     invariant: no row loss allowed).
  2. Backfill all existing rows so they have valid canonical values:
       * ``domain``      ← constant ``"wallet"``
       * ``entity_type`` ← constant ``"wallet"``
       * ``entity_id``   ← ``wallet_id`` (carries the same UUID)
       * ``action``      ← namespaced (``"wallet."`` + old enum value)
       * ``actor_id``    ← UUID if ``performed_by`` parses, else NULL
       * ``actor_label`` ← ``performed_by`` always (preserves human ID)
  3. Verify no row has NULL in domain/entity_type/entity_id/action
     after backfill — abort if so (data quality / migration bug).
  4. Verify the row count hasn't drifted.
  5. ALTER ``domain``, ``entity_type``, ``entity_id`` to NOT NULL.
  6. Convert ``action`` from the ``audit_action_enum`` type to a free
     ``String(128)`` (the canonical shape — services own their action
     vocabularies, no cross-service enum dependency).
  7. Demote ``reason`` from NOT NULL to nullable (canonical: some
     services don't require it).
  8. Drop the old ``wallet_id`` and ``performed_by`` columns plus the
     old ``ix_wallet_audit_logs_wallet_id`` index. Create the new
     ``ix_wallet_audit_entity_created`` composite index.
  9. Drop the now-orphaned ``audit_action_enum`` type.

Hand-written migration — required by project memory rule
``feedback_no_handwritten_migrations`` to use ``./scripts/db/migrate.sh
--manual`` (which is how this file was created). The body is manual
because Alembic's autogenerate can't represent the in-place data
backfill + column rename + type narrowing + enum drop sequence
correctly, and even where it can, splitting them into one
operationally-safe ordering is something only the author can do.

Revision ID: 5db2653c9ce8
Revises: f44dfaa2835f
Create Date: 2026-05-24 07:44:15.238508
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5db2653c9ce8"
down_revision = "f44dfaa2835f"
branch_labels = None
depends_on = None


# Pattern that PostgreSQL needs to validate a string before ``::uuid``
# without throwing. Mirrors the Python ``parse_uuid_or_none`` helper.
_UUID_REGEX = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Pre-flight row count — compliance invariant: no row may be
    #    lost during this migration.
    pre_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM wallet_audit_logs")
    ).scalar_one()

    # 2. Convert ``action`` from the audit_action_enum type to a free
    #    String(128) BEFORE the backfill. We need the column to accept
    #    arbitrary text so the backfill can write namespaced values
    #    (e.g. "wallet.freeze") that aren't in the enum's value list.
    #    The values stored at this point are still bare enum names
    #    (e.g. "freeze"); the next step namespaces them.
    op.alter_column(
        "wallet_audit_logs",
        "action",
        existing_type=sa.Enum(name="audit_action_enum"),
        type_=sa.String(length=128),
        existing_nullable=False,
        postgresql_using="action::text",
    )

    # 3. Backfill. Single UPDATE so every row transitions atomically.
    op.execute(
        sa.text(
            """
            UPDATE wallet_audit_logs
            SET
                domain = 'wallet',
                entity_type = 'wallet',
                entity_id = wallet_id,
                action = 'wallet.' || action,
                actor_id = CASE
                    WHEN performed_by ~ :uuid_re THEN performed_by::uuid
                    ELSE NULL
                END,
                actor_label = performed_by
            WHERE domain IS NULL
               OR entity_type IS NULL
               OR entity_id IS NULL
               OR actor_label IS NULL
               OR action NOT LIKE 'wallet.%'
            """
        ).bindparams(uuid_re=_UUID_REGEX)
    )

    # 4. Post-backfill correctness: all rows must have non-NULL values
    #    for the canonical NOT NULL columns AND a namespaced action.
    bad = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM wallet_audit_logs
            WHERE domain IS NULL
               OR entity_type IS NULL
               OR entity_id IS NULL
               OR action IS NULL
               OR action NOT LIKE 'wallet.%'
            """
        )
    ).scalar_one()
    if bad:
        raise RuntimeError(
            f"B4 backfill left {bad} wallet_audit_logs row(s) with "
            "NULL canonical columns or un-namespaced actions; "
            "refusing to ALTER NOT NULL"
        )

    # 5. Row-count invariant: no rows may have been lost.
    post_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM wallet_audit_logs")
    ).scalar_one()
    if post_count != pre_count:
        raise RuntimeError(
            f"B4 backfill changed row count: {pre_count} → {post_count}"
        )

    # 6. ALTER NOT NULL on the canonical required columns.
    op.alter_column(
        "wallet_audit_logs",
        "domain",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        "wallet_audit_logs",
        "entity_type",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "wallet_audit_logs",
        "entity_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

    # 7. Demote reason to optional-common.
    op.alter_column(
        "wallet_audit_logs",
        "reason",
        existing_type=sa.String(),
        nullable=True,
    )

    # 8. Drop old indexed column + the old standalone wallet_id index;
    #    create the new composite index on (entity_id, created_at).
    op.drop_index(
        op.f("ix_wallet_audit_logs_wallet_id"),
        table_name="wallet_audit_logs",
    )
    op.drop_column("wallet_audit_logs", "wallet_id")
    op.drop_column("wallet_audit_logs", "performed_by")
    op.create_index(
        "ix_wallet_audit_entity_created",
        "wallet_audit_logs",
        ["entity_id", "created_at"],
        unique=False,
    )

    # 9. The audit_action_enum type is now orphaned (only one column
    #    referenced it). Drop so future schema diffs stay clean.
    op.execute(sa.text("DROP TYPE IF EXISTS audit_action_enum"))


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Recreate the audit_action_enum type with the values the
    #    column originally held (lowercase per the standardize_enum
    #    migration a1b2c3d4e601). Old uppercase aliases included so
    #    rollback is exactly reversible with the prior schema.
    op.execute(
        sa.text(
            """
            CREATE TYPE audit_action_enum AS ENUM (
                'freeze', 'unfreeze', 'suspend', 'close',
                'admin_credit', 'admin_debit',
                'tier_change', 'limit_change'
            )
            """
        )
    )

    # 2. Re-add the old columns as nullable so we can backfill.
    op.add_column(
        "wallet_audit_logs",
        sa.Column("wallet_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "wallet_audit_logs",
        sa.Column("performed_by", sa.String(), nullable=True),
    )

    # 3. Reverse-backfill: copy back from the canonical columns.
    op.execute(
        sa.text(
            """
            UPDATE wallet_audit_logs
            SET
                wallet_id = entity_id,
                performed_by = actor_label
            """
        )
    )

    # 4. Verify reverse-backfill left no NULLs in the columns that
    #    will return to NOT NULL.
    bad = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM wallet_audit_logs
            WHERE wallet_id IS NULL OR performed_by IS NULL
            """
        )
    ).scalar_one()
    if bad:
        raise RuntimeError(
            f"B4 downgrade left {bad} row(s) with NULL wallet_id or "
            "performed_by; cannot restore NOT NULL"
        )

    # 5. Restore old NOT NULL on wallet_id/performed_by + reason.
    op.alter_column(
        "wallet_audit_logs",
        "wallet_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.alter_column(
        "wallet_audit_logs",
        "performed_by",
        existing_type=sa.String(),
        nullable=False,
    )
    op.alter_column(
        "wallet_audit_logs",
        "reason",
        existing_type=sa.String(),
        nullable=False,
    )

    # 6. Convert action back to the enum type. Strip the "wallet."
    #    namespace prefix so the value fits the enum.
    op.execute(
        sa.text(
            """
            UPDATE wallet_audit_logs
            SET action = substring(action FROM 8)
            WHERE action LIKE 'wallet.%'
            """
        )
    )
    op.alter_column(
        "wallet_audit_logs",
        "action",
        existing_type=sa.String(length=128),
        type_=sa.Enum(
            "freeze",
            "unfreeze",
            "suspend",
            "close",
            "admin_credit",
            "admin_debit",
            "tier_change",
            "limit_change",
            name="audit_action_enum",
        ),
        existing_nullable=False,
        postgresql_using="action::audit_action_enum",
    )

    # 7. Swap indexes back.
    op.drop_index(
        "ix_wallet_audit_entity_created",
        table_name="wallet_audit_logs",
    )
    op.create_index(
        op.f("ix_wallet_audit_logs_wallet_id"),
        "wallet_audit_logs",
        ["wallet_id"],
        unique=False,
    )

    # 8. Drop the canonical columns added in stage 1.
    op.drop_column("wallet_audit_logs", "actor_label")
    op.drop_column("wallet_audit_logs", "actor_id")
    op.drop_column("wallet_audit_logs", "entity_id")
    op.drop_column("wallet_audit_logs", "entity_type")
    op.drop_column("wallet_audit_logs", "domain")
