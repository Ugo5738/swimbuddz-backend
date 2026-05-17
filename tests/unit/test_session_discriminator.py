"""Unit tests for the Session ``session_type`` ↔ context-FK discriminator rule.

Exercises ``services.sessions_service.models._validators.validate_session_discriminator``
directly (no DB, no FastAPI). Confirms each SessionType branch and the
"god object" cases the May 2026 review flagged are now caught.

Phase 3.1 (2026-05-17) dropped the aspirational ONE_ON_ONE / GROUP_BOOKING
SessionType values and the unused ``booking_id`` column — see
docs/design/A1_SESSION_DISCRIMINATOR_REFACTOR.md. The tests that previously
exercised those branches were removed; the remaining four
SessionType values (COHORT_CLASS, EVENT, CLUB, COMMUNITY) are still
covered for both happy-path and forbidden-FK rejections.
"""

import uuid

import pytest

from services.sessions_service.models._validators import (
    SessionDiscriminatorError,
    validate_session_discriminator,
)
from services.sessions_service.models.enums import SessionType


def _u() -> uuid.UUID:
    return uuid.uuid4()


# --------------------------------------------------------------------------
# Happy paths — one per SessionType
# --------------------------------------------------------------------------


class TestValidCombinations:
    def test_cohort_class_with_cohort_id(self):
        validate_session_discriminator(
            session_type=SessionType.COHORT_CLASS,
            cohort_id=_u(),
            event_id=None,
            pod_id=None,
        )

    def test_event_with_event_id(self):
        validate_session_discriminator(
            session_type=SessionType.EVENT,
            cohort_id=None,
            event_id=_u(),
            pod_id=None,
        )

    def test_club_without_pod_id_is_general_club_session(self):
        # NULL pod_id is valid for CLUB → "general" club session.
        validate_session_discriminator(
            session_type=SessionType.CLUB,
            cohort_id=None,
            event_id=None,
            pod_id=None,
        )

    def test_club_with_pod_id_is_pod_scoped_club_session(self):
        validate_session_discriminator(
            session_type=SessionType.CLUB,
            cohort_id=None,
            event_id=None,
            pod_id=_u(),
        )

    def test_community_with_no_fks(self):
        validate_session_discriminator(
            session_type=SessionType.COMMUNITY,
            cohort_id=None,
            event_id=None,
            pod_id=None,
        )


# --------------------------------------------------------------------------
# Required-FK-missing rejections
# --------------------------------------------------------------------------


class TestRequiredFKMissing:
    def test_cohort_class_without_cohort_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="cohort_id"):
            validate_session_discriminator(
                session_type=SessionType.COHORT_CLASS,
                cohort_id=None,
                event_id=None,
                pod_id=None,
            )

    def test_event_without_event_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="event_id"):
            validate_session_discriminator(
                session_type=SessionType.EVENT,
                cohort_id=None,
                event_id=None,
                pod_id=None,
            )


# --------------------------------------------------------------------------
# "God object" cases — wrong FK set for the type
# --------------------------------------------------------------------------


class TestForbiddenFKCombinations:
    def test_cohort_class_with_event_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="must not set event_id"):
            validate_session_discriminator(
                session_type=SessionType.COHORT_CLASS,
                cohort_id=_u(),
                event_id=_u(),
                pod_id=None,
            )

    def test_cohort_class_with_pod_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="must not set pod_id"):
            validate_session_discriminator(
                session_type=SessionType.COHORT_CLASS,
                cohort_id=_u(),
                event_id=None,
                pod_id=_u(),
            )

    def test_event_with_cohort_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="must not set cohort_id"):
            validate_session_discriminator(
                session_type=SessionType.EVENT,
                cohort_id=_u(),
                event_id=_u(),
                pod_id=None,
            )

    def test_club_with_cohort_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="must not set cohort_id"):
            validate_session_discriminator(
                session_type=SessionType.CLUB,
                cohort_id=_u(),
                event_id=None,
                pod_id=None,
            )

    def test_club_with_event_id_rejected(self):
        with pytest.raises(SessionDiscriminatorError, match="must not set event_id"):
            validate_session_discriminator(
                session_type=SessionType.CLUB,
                cohort_id=None,
                event_id=_u(),
                pod_id=None,
            )

    def test_community_with_any_fk_rejected(self):
        # All three FKs are forbidden for COMMUNITY. Check at least one of each.
        for fk_kwargs in [
            {"cohort_id": _u()},
            {"event_id": _u()},
            {"pod_id": _u()},
        ]:
            base = {
                "session_type": SessionType.COMMUNITY,
                "cohort_id": None,
                "event_id": None,
                "pod_id": None,
            }
            base.update(fk_kwargs)
            with pytest.raises(
                SessionDiscriminatorError, match="must not set any context FK"
            ):
                validate_session_discriminator(**base)


# --------------------------------------------------------------------------
# Pydantic + SQLAlchemy integration smoke
# --------------------------------------------------------------------------


class TestPydanticWiring:
    def test_session_create_rejects_bad_combo_with_validation_error(self):
        """SessionCreate's @model_validator surfaces ValueError, which
        Pydantic turns into a 422 at the FastAPI layer."""
        from datetime import datetime, timezone

        from pydantic import ValidationError

        from services.sessions_service.schemas import SessionCreate

        with pytest.raises(ValidationError) as exc_info:
            SessionCreate(
                title="Bad",
                session_type=SessionType.COHORT_CLASS,
                starts_at=datetime.now(timezone.utc),
                ends_at=datetime.now(timezone.utc),
                # COHORT_CLASS requires cohort_id; not setting it must fail.
            )
        assert "cohort_id" in str(exc_info.value)
