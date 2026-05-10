"""rls_and_realtime_for_chat

Revision ID: 28be07d7e61e
Revises: 8538d428974f
Create Date: 2026-05-06 09:31:07.940427

Enables Row-Level Security on chat tables so Supabase Realtime subscribers
only see channels they belong to, then adds the streamable tables to the
``supabase_realtime`` publication.

Context:

* Our backend connects to Postgres directly (not via Supabase REST), so RLS
  does NOT apply to API code paths — service-role connections bypass RLS by
  design. RLS here gates ONLY Supabase Realtime subscriptions and any
  potential future direct-from-client REST calls.
* Auth comes through as ``auth.uid()`` (uuid). Chat tables key on
  ``member_id`` (members-service id, separate uuid). Policies join through
  ``members.auth_id`` (text) to bridge the two.
* No INSERT / UPDATE / DELETE policies are defined: clients can subscribe
  and read, but every mutation must come through the chat API (service-role
  JWT bypasses RLS).
* Publication adds are wrapped in a DO block — if ``supabase_realtime``
  doesn't exist (e.g. local Postgres without Supabase) the migration is a
  no-op for that step rather than failing.
"""

from alembic import op
import sqlalchemy as sa  # noqa: F401  (kept for autogenerate parity)


# revision identifiers, used by Alembic.
revision = "28be07d7e61e"
down_revision = "8538d428974f"
branch_labels = None
depends_on = None


# Tables that get RLS. Order matters only for readability.
_RLS_TABLES = [
    "chat_channels",
    "chat_channel_members",
    "chat_messages",
    "chat_message_reactions",
    "chat_message_reports",
    "chat_audit_log",
]

# Tables we publish to Supabase Realtime. We deliberately exclude the audit
# log and reports — those are admin surfaces, never streamed to members.
_REALTIME_TABLES = [
    "chat_messages",
    "chat_message_reactions",
    "chat_channel_members",
]


def upgrade() -> None:
    # 1. Enable RLS. FORCE so even table owners go through policies — Supabase
    #    service_role still bypasses via its BYPASSRLS attribute.
    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # 2. SELECT policies. Pattern: "are you an active member of the channel?"
    #    expressed as a join through members.auth_id (text) = auth.uid()::text.

    op.execute(
        """
        CREATE POLICY chat_channels_select ON chat_channels
        FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM chat_channel_members ccm
                JOIN members m ON m.id = ccm.member_id
                WHERE ccm.channel_id = chat_channels.id
                  AND ccm.left_at IS NULL
                  AND m.auth_id = auth.uid()::text
            )
        )
        """
    )

    # Members see other members' rows in channels they share (so the
    # participant list renders), and always their own row (covers the case
    # where they were just removed but are still listening).
    op.execute(
        """
        CREATE POLICY chat_channel_members_select ON chat_channel_members
        FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM chat_channel_members ccm
                JOIN members m ON m.id = ccm.member_id
                WHERE ccm.channel_id = chat_channel_members.channel_id
                  AND ccm.left_at IS NULL
                  AND m.auth_id = auth.uid()::text
            )
            OR EXISTS (
                SELECT 1 FROM members m
                WHERE m.id = chat_channel_members.member_id
                  AND m.auth_id = auth.uid()::text
            )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY chat_messages_select ON chat_messages
        FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM chat_channel_members ccm
                JOIN members m ON m.id = ccm.member_id
                WHERE ccm.channel_id = chat_messages.channel_id
                  AND ccm.left_at IS NULL
                  AND m.auth_id = auth.uid()::text
            )
        )
        """
    )

    # Reactions are visible iff the underlying message is visible.
    op.execute(
        """
        CREATE POLICY chat_message_reactions_select ON chat_message_reactions
        FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1
                FROM chat_messages cm
                JOIN chat_channel_members ccm ON ccm.channel_id = cm.channel_id
                JOIN members m ON m.id = ccm.member_id
                WHERE cm.id = chat_message_reactions.message_id
                  AND ccm.left_at IS NULL
                  AND m.auth_id = auth.uid()::text
            )
        )
        """
    )

    # Reports: only the reporter sees their own. Admin queue is API-only and
    # uses the service-role bypass.
    op.execute(
        """
        CREATE POLICY chat_message_reports_select_own ON chat_message_reports
        FOR SELECT TO authenticated
        USING (
            EXISTS (
                SELECT 1 FROM members m
                WHERE m.id = chat_message_reports.reporter_id
                  AND m.auth_id = auth.uid()::text
            )
        )
        """
    )

    # No SELECT policy on chat_audit_log — clients can never read it. Admins
    # use the API, which bypasses RLS via service-role.

    # 3. Add streamable tables to the Supabase Realtime publication. Wrapped
    #    in a DO block so non-Supabase environments don't break the migration.
    for table in _REALTIME_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'
                ) THEN
                    ALTER PUBLICATION supabase_realtime ADD TABLE {table};
                END IF;
            EXCEPTION WHEN duplicate_object THEN
                -- Already in the publication — fine.
                NULL;
            END $$;
            """
        )


def downgrade() -> None:
    for table in _REALTIME_TABLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'
                ) THEN
                    ALTER PUBLICATION supabase_realtime DROP TABLE {table};
                END IF;
            EXCEPTION WHEN undefined_object THEN
                NULL;
            END $$;
            """
        )

    op.execute(
        "DROP POLICY IF EXISTS chat_message_reports_select_own ON chat_message_reports"
    )
    op.execute(
        "DROP POLICY IF EXISTS chat_message_reactions_select ON chat_message_reactions"
    )
    op.execute("DROP POLICY IF EXISTS chat_messages_select ON chat_messages")
    op.execute(
        "DROP POLICY IF EXISTS chat_channel_members_select ON chat_channel_members"
    )
    op.execute("DROP POLICY IF EXISTS chat_channels_select ON chat_channels")

    for table in _RLS_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
