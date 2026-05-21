"""Synthetic principal identifiers for non-human actors in audit logs.

When automated systems (the AI service today; potentially scheduled jobs,
webhook handlers, or other service-account paths in future) write rows
that record *who* did something — e.g. ``milestone_review_events.actor_id``,
``media_audit_logs.actor_id`` — we need a stable UUID to write there.

We deliberately do **not** use the JWT's ``user_id`` claim from a
service-role token (it represents whichever token was issued, not the
logical actor — so "all AI activity" becomes a moving target), and we
deliberately do **not** create real ``auth.users`` rows for services
(that's full service-account identity management — scope creep until
the platform actually needs it).

Instead, each non-human principal has a stable, hardcoded UUID here.
The constants are used purely for **attribution** in audit rows — they
are NOT used for authentication. Authentication still flows through
the existing ``require_admin`` / ``require_service_role`` dependencies
in ``libs.auth``. Attribution and authentication are different
concerns and don't need the same value.

The ``00000000-0000-0000-0000-000000000001`` shape is intentional:
visually obvious in DB inspection that the row references an
automated principal rather than a real person. Future principals
continue the pattern (``...000002``, ``...000003``, …).

If at some point real service identities become necessary (e.g. for
fine-grained RLS on writes), the principal UUIDs here can be promoted
to ``auth.users`` rows without rewriting any historical audit data —
the UUIDs are stable.

See ``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §6.1 for the
design rationale.
"""

from __future__ import annotations

from uuid import UUID

# ── Non-human principals ──────────────────────────────────────────────

#: The AI service. Used as ``actor_id`` on audit rows produced by
#: automated AI scoring/override paths.
AI_SERVICE_PRINCIPAL_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")


# ── Display labels ────────────────────────────────────────────────────

#: Human-readable label for each principal. Used by admin UIs that
#: surface audit history so reviewers see "AI Service" instead of a
#: bare UUID. Consumers should fall back to a generic "Automated"
#: label for any unknown principal UUID rather than crashing.
PRINCIPAL_LABELS: dict[UUID, str] = {
    AI_SERVICE_PRINCIPAL_ID: "AI Service",
    # future synthetic principals slot in here
}


def is_synthetic_principal(actor_id: UUID | None) -> bool:
    """Return True if ``actor_id`` is one of our registered synthetic
    (non-human) principals.

    Useful for UI logic that needs to show "system action" badges or
    skip user-profile lookups that would 404 for synthetic IDs.
    """
    return actor_id is not None and actor_id in PRINCIPAL_LABELS


def principal_label(actor_id: UUID | None, fallback: str = "Automated") -> str:
    """Return the display label for a synthetic principal, or ``fallback``
    if ``actor_id`` is unknown or None.

    Callers wanting "treat unknown actors as human users" should not use
    this — they should look up the user record by ``actor_id`` and only
    fall back to ``principal_label`` if no user was found.
    """
    if actor_id is None:
        return fallback
    return PRINCIPAL_LABELS.get(actor_id, fallback)


__all__ = [
    "AI_SERVICE_PRINCIPAL_ID",
    "PRINCIPAL_LABELS",
    "is_synthetic_principal",
    "principal_label",
]
