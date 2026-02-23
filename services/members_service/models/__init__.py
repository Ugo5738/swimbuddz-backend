"""Members Service models package.

Re-exports all models and enums so that:
  - ``from services.members_service.models import Member`` works unchanged
  - Alembic env.py imports continue to work without modification
  - SQLAlchemy's mapper registry sees every model class on import

All model definitions live in models/core.py.
"""

from services.members_service.models.core import (  # noqa: F401
    AgreementVersion,
    ClubChallenge,
    CoachAgreement,
    CoachBankAccount,
    CoachGrade,
    CoachProfile,
    HandbookVersion,
    Member,
    MemberAvailability,
    MemberChallengeCompletion,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    PendingRegistration,
    VolunteerInterest,
    VolunteerRole,
)

__all__ = [
    "CoachGrade",
    "Member",
    "MemberProfile",
    "MemberEmergencyContact",
    "MemberAvailability",
    "MemberMembership",
    "MemberPreferences",
    "PendingRegistration",
    "VolunteerRole",
    "VolunteerInterest",
    "ClubChallenge",
    "MemberChallengeCompletion",
    "CoachProfile",
    "CoachAgreement",
    "AgreementVersion",
    "HandbookVersion",
    "CoachBankAccount",
]
