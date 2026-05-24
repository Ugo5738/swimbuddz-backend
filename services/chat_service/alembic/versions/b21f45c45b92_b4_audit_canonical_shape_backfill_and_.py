"""B4 audit canonical shape: stage 2 — backfill, NOT NULL, drop legacy.

Hand-written migration — autogenerate cannot represent the per-action
backfill needed before flipping NOT NULL on the new columns. Stage 1
(``b0ca76a22b55_b4_audit_canonical_shape_add_new_columns.py``) added
the canonical columns as nullable; this stage:

1. Converts ``action`` from ``chat_audit_action_enum`` → ``String(128)``
   FIRST (Postgres rejects writing namespaced text into an enum column).
2. Backfills every existing row's canonical columns from the legacy
   columns, using per-``ChatAuditAction`` CASE arms for
   ``entity_type`` and a COALESCE chain across legacy scope refs
   (``message_id`` / ``channel_id`` / ``subject_member_id``) for
   ``entity_id``. The ordering of the COALESCE matches the per-action
   priority used by the Python writer in
   ``services.chat_service.services.audit_log._ENTITY_MAP``.
3. **Payload split is non-lossy**: every existing payload becomes
   ``new_value`` (preserves data without inventing a fake "old" half).
   Forward writers may populate ``old_value`` / ``new_value`` properly
   for diff actions like ``role_changed`` / ``message_edited``; the
   legacy ``payload`` column was a single bag and we can't sort it out
   per-row without action-specific knowledge that's encoded in
   admin/moderator code, not in the audit row itself.
4. Namespaces ``action`` to ``'chat.<verb>'`` to match canonical shape.
5. **Row-count invariant**: refuses to ALTER NOT NULL unless every row
   has populated ``domain`` / ``entity_type`` / ``entity_id``.
6. ALTERs the three NOT-NULL canonical columns + drops legacy
   ``payload`` / ``message_id`` + the ``chat_audit_action_enum`` type.

Downgrade is lossy on namespaced action verbs that aren't in the
original enum vocabulary (e.g. any new action added after this
migration) — acceptable because downgrade is for emergency rollback,
not routine ops.
"""

revision = 'b21f45c45b92'
down_revision = 'b0ca76a22b55'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


# Canonical (legacy) enum values for chat_audit_action_enum, used to
# rebuild the type on downgrade.
LEGACY_ACTION_VALUES = (
    'message_sent',
    'message_edited',
    'message_deleted',
    'channel_joined',
    'channel_left',
    'member_added',
    'member_removed',
    'role_changed',
    'report_filed',
    'report_resolved',
    'safeguarding_action',
    'channel_archived',
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── Pre-flight: capture pre-backfill row count for invariant check ─
    pre_count = conn.execute(
        sa.text("SELECT count(*) FROM chat_audit_log")
    ).scalar()

    # ── 1. Convert action enum → String(128) BEFORE backfill ──────────
    # We need to write namespaced 'chat.<verb>' strings into this column
    # in step 4; PostgreSQL won't cast text→enum if the value isn't
    # already an enum member. Doing the type conversion first sidesteps
    # the issue and keeps the backfill UPDATE simple.
    op.execute(
        "ALTER TABLE chat_audit_log "
        "ALTER COLUMN action TYPE varchar(128) "
        "USING action::text"
    )

    # ── 2-4. Per-action backfill ──────────────────────────────────────
    # entity_type and entity_id depend on which ChatAuditAction the row
    # records; the CASE arms mirror _ENTITY_MAP in
    # services/chat_service/services/audit_log.py.
    op.execute(
        sa.text(
            """
            UPDATE chat_audit_log
            SET domain = 'chat',
                actor_label = NULL,
                old_value = NULL,
                new_value = COALESCE(payload, '{}'::jsonb),
                reason = NULL,
                entity_type = CASE
                    WHEN action IN ('message_sent', 'message_edited',
                                    'message_deleted')
                        THEN 'message'
                    WHEN action IN ('channel_joined', 'channel_left',
                                    'channel_archived')
                        THEN 'channel'
                    WHEN action IN ('member_added', 'member_removed',
                                    'role_changed')
                        THEN 'channel_membership'
                    WHEN action IN ('report_filed', 'report_resolved')
                        THEN 'report'
                    WHEN action = 'safeguarding_action'
                        THEN 'safeguarding'
                    ELSE 'unknown'
                END,
                entity_id = CASE
                    WHEN action IN ('message_sent', 'message_edited',
                                    'message_deleted')
                        THEN message_id
                    WHEN action IN ('channel_joined', 'channel_left',
                                    'channel_archived')
                        THEN channel_id
                    WHEN action IN ('member_added', 'member_removed',
                                    'role_changed')
                        THEN COALESCE(subject_member_id, channel_id)
                    WHEN action IN ('report_filed', 'report_resolved')
                        THEN COALESCE(message_id, subject_member_id,
                                      channel_id)
                    WHEN action = 'safeguarding_action'
                        THEN COALESCE(message_id, channel_id,
                                      subject_member_id)
                    ELSE COALESCE(message_id, channel_id,
                                  subject_member_id, actor_id)
                END,
                action = 'chat.' || action
            """
        )
    )

    # ── 5. Row-count invariant ────────────────────────────────────────
    bad = conn.execute(
        sa.text(
            "SELECT count(*) FROM chat_audit_log "
            "WHERE domain IS NULL "
            "OR entity_type IS NULL "
            "OR entity_id IS NULL"
        )
    ).scalar()
    if bad:
        raise RuntimeError(
            f"B4 chat backfill row-count invariant failed: {bad} of "
            f"{pre_count} rows still have NULL canonical columns "
            f"(domain / entity_type / entity_id). Likely cause: an "
            f"action row had no message_id/channel_id/subject_member_id "
            f"to derive entity_id from. Inspect with: "
            f"SELECT id, action, channel_id, message_id, "
            f"subject_member_id FROM chat_audit_log WHERE entity_id IS NULL;"
        )

    # ── 6. ALTER NOT NULL on backfilled canonical columns ─────────────
    op.alter_column(
        'chat_audit_log', 'domain',
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.alter_column(
        'chat_audit_log', 'entity_type',
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        'chat_audit_log', 'entity_id',
        existing_type=sa.UUID(),
        nullable=False,
    )

    # ── 7. Drop legacy columns + the action enum type ─────────────────
    op.drop_column('chat_audit_log', 'payload')
    op.drop_column('chat_audit_log', 'message_id')
    op.execute("DROP TYPE IF EXISTS chat_audit_action_enum")


def downgrade() -> None:
    # Recreate the action enum type.
    legacy_values_sql = ", ".join(f"'{v}'" for v in LEGACY_ACTION_VALUES)
    op.execute(
        f"CREATE TYPE chat_audit_action_enum AS ENUM ({legacy_values_sql})"
    )

    # Re-add legacy columns.
    op.add_column(
        'chat_audit_log',
        sa.Column('message_id', sa.UUID(), nullable=True),
    )
    op.add_column(
        'chat_audit_log',
        sa.Column(
            'payload',
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    # Reverse backfill — strip the 'chat.' namespace and put new_value
    # back into payload. message_id is best-effort: only set when the
    # canonical entity_type was 'message'.
    op.execute(
        """
        UPDATE chat_audit_log
        SET payload = COALESCE(new_value, old_value, '{}'::jsonb),
            message_id = CASE
                WHEN entity_type = 'message' THEN entity_id
                ELSE NULL
            END,
            action = CASE
                WHEN action LIKE 'chat.%' THEN substring(action FROM 6)
                ELSE action
            END
        """
    )

    # Convert action back to enum — fails if any row has a verb that
    # wasn't in LEGACY_ACTION_VALUES (acceptable downgrade constraint).
    op.execute(
        "ALTER TABLE chat_audit_log "
        "ALTER COLUMN action TYPE chat_audit_action_enum "
        "USING action::chat_audit_action_enum"
    )

    # Relax canonical NOT NULLs back to nullable (stage 1 added them
    # as nullable; stage 1's downgrade will drop them entirely).
    op.alter_column(
        'chat_audit_log', 'entity_id',
        existing_type=sa.UUID(),
        nullable=True,
    )
    op.alter_column(
        'chat_audit_log', 'entity_type',
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.alter_column(
        'chat_audit_log', 'domain',
        existing_type=sa.String(length=32),
        nullable=True,
    )
