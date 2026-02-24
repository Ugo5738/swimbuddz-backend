"""Members Service schemas package.

Re-exports all schemas so that:
  - ``from services.members_service.schemas import MemberResponse`` works
  - All router files use a single import namespace

Schema files:
  - schemas/member.py  — Member schemas (from schemas.py)
  - schemas/coach.py   — Coach schemas (from coach_schemas.py)
  - schemas/challenge.py — Volunteer/challenge schemas (from volunteer_schemas.py)
"""

from services.members_service.schemas.challenge import (  # noqa: F401
    ChallengeCompletionCreate,
    ChallengeCompletionResponse,
    ClubChallengeBase,
    ClubChallengeCreate,
    ClubChallengeResponse,
    ClubChallengeUpdate,
    VolunteerInterestCreate,
    VolunteerInterestResponse,
    VolunteerRoleBase,
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
)
from services.members_service.schemas.coach import (  # noqa: F401
    AdminApproveCoach,
    AdminCoachApplicationDetail,
    AdminCoachApplicationListItem,
    AdminRejectCoach,
    AdminRequestMoreInfo,
    AdminUpdateCoachGrades,
    AgreementContentResponse,
    AgreementVersionDetail,
    AgreementVersionListItem,
    BankAccountCreate,
    BankAccountResponse,
    BankListResponse,
    CoachAgreementHistoryItem,
    CoachAgreementResponse,
    CoachAgreementStatusResponse,
    CoachApplicationCreate,
    CoachApplicationResponse,
    CoachApplicationStatusResponse,
    CoachCategoryGradeUpdate,
    CoachEligibilityCheck,
    CoachGradeEnum,
    CoachGradesResponse,
    CoachOnboardingUpdate,
    CoachPreferencesUpdate,
    CoachProfileUpdate,
    CoachProgressionStats,
    CreateAgreementVersionRequest,
    CreateHandbookVersionRequest,
    EligibleCoachListItem,
    HandbookContentResponse,
    HandbookVersionDetail,
    HandbookVersionListItem,
    ProgramCategoryEnum,
    ResolveAccountRequest,
    ResolveAccountResponse,
    SignAgreementRequest,
    SignatureTypeEnum,
)
from services.members_service.schemas.member import (  # noqa: F401
    ActivateAcademyRequest,
    ActivateClubRequest,
    ActivateCommunityRequest,
    ApprovalAction,
    CoachProfileResponse,
    ExtendCommunityRequest,
    MemberAvailabilityInput,
    MemberAvailabilityResponse,
    MemberBasicResponse,
    MemberCreate,
    MemberDirectoryResponse,
    MemberEmergencyContactInput,
    MemberEmergencyContactResponse,
    MemberListResponse,
    MemberMembershipInput,
    MemberMembershipResponse,
    MemberPreferencesInput,
    MemberPreferencesResponse,
    MemberProfileInput,
    MemberProfileResponse,
    MemberPublicResponse,
    MemberResponse,
    MemberUpdate,
    PendingMemberResponse,
    PendingRegistrationCreate,
    PendingRegistrationResponse,
)
