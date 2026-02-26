"""Members Service models package.

Re-exports all models and enums so that:
  - ``from services.members_service.models import Member`` works unchanged
  - Alembic env.py imports continue to work without modification
  - SQLAlchemy's mapper registry sees every model class on import

Model definitions are split across:
  - models/member.py   — Member and related sub-tables
  - models/coach.py    — Coach profile, agreements, handbook, bank accounts
  - models/volunteer.py — Legacy volunteer roles and club challenges
"""

from services.members_service.models.coach import (  # noqa: F401
    AgreementVersion,
    CoachAgreement,
    CoachBankAccount,
    CoachProfile,
    HandbookVersion,
)
from services.members_service.models.enums import CoachGrade  # noqa: F401
from services.members_service.models.member import (  # noqa: F401
    Member,
    MemberAvailability,
    MemberEmergencyContact,
    MemberMembership,
    MemberPreferences,
    MemberProfile,
    PendingRegistration,
)
from services.members_service.models.volunteer import (  # noqa: F401
    ClubChallenge,
    MemberChallengeCompletion,
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
