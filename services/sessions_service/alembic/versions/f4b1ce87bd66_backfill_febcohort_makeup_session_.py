"""backfill_febcohort_makeup_session_cohort_id_then_validate_discriminator

Revision ID: f4b1ce87bd66
Revises: 342fc616217e
Create Date: 2026-05-17 15:09:55.059451

Hand-written data migration (A1 Phase 2.b). Generated via
`./scripts/db/migrate.sh --manual` so the revision id is Alembic-assigned
and the chain stays intact; body is hand-written because this is a
targeted data reconciliation Alembic autogenerate cannot represent.

Context — the only production rows that violate the new 4-branch
``ck_sessions_discriminator`` (added NOT VALID in 0cfeb8c4ddfb) are 5
``cohort_class`` sessions with a NULL ``cohort_id``. They are the
"Make-Up Swim" catch-up classes the team ran for students enrolled in
the **Feb 2026 cohort** (``671c0988-1206-4b34-95dd-9562b8a3518a`` —
"Beginner Freestyle: Zero to 50 Meters - Feb 2026", 2026-02-28 →
2026-05-23). All 5 session dates fall inside that cohort's window.
They were created as generic ``cohort_class`` rows before the
discriminator existed and never got their ``cohort_id`` set.

Fix: link the 5 sessions to the Feb cohort (keeps the correct
``cohort_class`` type, satisfies the discriminator, and makes them
correctly surface under that cohort in academy views), then
``VALIDATE CONSTRAINT`` so the rule is fully enforced going forward.

The UPDATE is pinned to the 5 exact session ids AND guarded
(``session_type='cohort_class' AND cohort_id IS NULL``), so it is
idempotent and a strict no-op in any environment that does not contain
these specific prod rows (dev/staging get a fresh ``reset.sh``).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f4b1ce87bd66"
down_revision = "342fc616217e"
branch_labels = None
depends_on = None

_FEB_COHORT_ID = "671c0988-1206-4b34-95dd-9562b8a3518a"

# The 5 "Make-Up Swim" sessions for Feb-2026-cohort students.
_MAKEUP_SESSION_IDS = (
    "fcc14c96-f6cf-4bae-9963-4abfc5f2d07d",  # MakeUp Swim — 2026-04-03
    "8def741a-ade2-4e90-b1a2-a94c0f74beee",  # Makeup Swim — 2026-04-04
    "3be81b4e-34b7-4d12-a7cd-462347bf464d",  # Friday Make-Up Swim — 2026-05-08
    "abeac924-4e1c-40e9-83c9-75adc332b4f8",  # Saturday Make Up Cohort Session — 2026-05-09
    "048cfdd2-1266-46c5-88dd-7debb196e12c",  # Saturday Make Up Swim — 2026-05-16
)

# Same 4-branch expression as 0cfeb8c4ddfb (_NEW_CHECK). Duplicated by
# design — a migration is an immutable snapshot and must not import
# another revision's module-level constants.
_NEW_CHECK = (
    "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
    "AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'event' AND event_id IS NOT NULL "
    "AND cohort_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'club' "
    "AND cohort_id IS NULL AND event_id IS NULL) "
    "OR (session_type = 'community' "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL)"
)

_ID_LIST = ", ".join(f"'{sid}'::uuid" for sid in _MAKEUP_SESSION_IDS)


def upgrade() -> None:
    # 1. Reconcile the 5 makeup sessions onto the Feb cohort. Guarded so
    #    it only touches rows still in the violating state.
    op.execute(
        f"UPDATE sessions "
        f"SET cohort_id = '{_FEB_COHORT_ID}'::uuid "
        f"WHERE id IN ({_ID_LIST}) "
        f"AND session_type = 'cohort_class' "
        f"AND cohort_id IS NULL"
    )
    # 2. Data is now clean — promote the NOT VALID constraint to fully
    #    validated so every existing row is checked and the rule is
    #    enforced from here on.
    op.execute(
        "ALTER TABLE sessions VALIDATE CONSTRAINT ck_sessions_discriminator"
    )


def downgrade() -> None:
    # Restore the pre-migration state: constraint back to NOT VALID and
    # the 5 sessions back to NULL cohort_id (which the validated
    # constraint would reject, hence the drop/re-add-NOT-VALID dance).
    op.execute("ALTER TABLE sessions DROP CONSTRAINT ck_sessions_discriminator")
    op.execute(
        f"UPDATE sessions SET cohort_id = NULL "
        f"WHERE id IN ({_ID_LIST}) "
        f"AND cohort_id = '{_FEB_COHORT_ID}'::uuid"
    )
    op.execute(
        f"ALTER TABLE sessions ADD CONSTRAINT ck_sessions_discriminator "
        f"CHECK ({_NEW_CHECK}) NOT VALID"
    )
