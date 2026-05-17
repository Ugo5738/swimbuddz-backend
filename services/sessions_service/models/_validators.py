"""Cross-column validation for the Session model.

The ``sessions`` table carries a ``session_type`` discriminator plus three
mutually-exclusive context FK columns (``cohort_id``, ``event_id``,
``pod_id``). Historically nothing enforced the type → FK mapping — any
combination was writeable, which the May 2026 codebase review flagged as
a "god object" smell.

This module is the canonical enforcement layer. It is consumed in two
places to give us defence-in-depth:

* Pydantic ``@model_validator`` on ``SessionCreate`` — catches the
  malformed request at API entry with a clean 422 response.
* SQLAlchemy ``before_insert`` / ``before_update`` event listener on the
  ``Session`` model — catches any non-API writer (seed scripts, internal
  services, future MCP tools) and raises before the row is flushed.

A DB-level CHECK constraint (Phase 2) backs both layers and is the
ultimate source of truth.

Phase 3.1 (2026-05-17) dropped the aspirational ONE_ON_ONE / GROUP_BOOKING
types and the unused ``booking_id`` column. Private 1-on-1 and small-group
academy instruction is expressed via ``Cohort.type`` (CohortType.PRIVATE,
SMALL_GROUP, CORPORATE) — all such sessions stay SessionType.COHORT_CLASS.
See docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md.
"""

from __future__ import annotations

import uuid
from typing import Optional

from services.sessions_service.models.enums import SessionType


class SessionDiscriminatorError(ValueError):
    """Raised when a Session's session_type ↔ context-FK combination is invalid."""


# Type-specific rules, expressed as (required_fk_name, set_of_fks_that_must_be_null).
# pod_id is *optional* for CLUB sessions, so the rule for CLUB enumerates
# explicitly rather than going through this table.
_REQUIRED_BY_TYPE: dict[SessionType, tuple[str, frozenset[str]]] = {
    SessionType.COHORT_CLASS: (
        "cohort_id",
        frozenset({"event_id", "pod_id"}),
    ),
    SessionType.EVENT: (
        "event_id",
        frozenset({"cohort_id", "pod_id"}),
    ),
}


def validate_session_discriminator(
    *,
    session_type: SessionType,
    cohort_id: Optional[uuid.UUID],
    event_id: Optional[uuid.UUID],
    pod_id: Optional[uuid.UUID],
) -> None:
    """Enforce the ``session_type`` → context-FK mapping.

    Rules:
      * ``COHORT_CLASS`` — ``cohort_id`` required; ``event_id`` and
        ``pod_id`` must be NULL.
      * ``EVENT`` — ``event_id`` required; ``cohort_id`` and ``pod_id``
        must be NULL.
      * ``CLUB`` — ``cohort_id`` and ``event_id`` must be NULL;
        ``pod_id`` is *optional* (NULL = general club session; set =
        pod-scoped club session).
      * ``COMMUNITY`` — all three context FKs must be NULL.

    Raises ``SessionDiscriminatorError`` (a ``ValueError`` subclass) on
    violation so callers get a clean 422 when the validator is wired
    into Pydantic, or a transactional rollback when wired into the
    SQLAlchemy event listener.
    """
    fks = {
        "cohort_id": cohort_id,
        "event_id": event_id,
        "pod_id": pod_id,
    }

    if session_type in _REQUIRED_BY_TYPE:
        required, forbidden = _REQUIRED_BY_TYPE[session_type]
        if fks[required] is None:
            raise SessionDiscriminatorError(
                f"session_type={session_type.value!r} requires {required} to be set"
            )
        for fk in forbidden:
            if fks[fk] is not None:
                raise SessionDiscriminatorError(
                    f"session_type={session_type.value!r} must not set {fk} "
                    f"(only {required} applies for this type)"
                )
        return

    if session_type is SessionType.CLUB:
        # pod_id is optional for CLUB; the other two context FKs must be NULL.
        for fk in ("cohort_id", "event_id"):
            if fks[fk] is not None:
                raise SessionDiscriminatorError(
                    f"session_type='club' must not set {fk} "
                    f"(only pod_id applies, and it is optional)"
                )
        return

    if session_type is SessionType.COMMUNITY:
        for fk, value in fks.items():
            if value is not None:
                raise SessionDiscriminatorError(
                    f"session_type='community' must not set any context FK "
                    f"(got {fk}={value!r})"
                )
        return

    # New SessionType added to the enum without an entry here.
    raise SessionDiscriminatorError(
        f"No discriminator rule defined for session_type={session_type.value!r}"
    )


__all__ = ["SessionDiscriminatorError", "validate_session_discriminator"]
