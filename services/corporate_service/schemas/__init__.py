"""Corporate service schemas package."""

from services.corporate_service.schemas.contact import (
    CorporateContactCreate,
    CorporateContactListResponse,
    CorporateContactResponse,
    CorporateContactUpdate,
    PublicLeadCreate,
    PublicLeadResponse,
)
from services.corporate_service.schemas.deal import (
    CorporateDealCreate,
    CorporateDealListResponse,
    CorporateDealLossRequest,
    CorporateDealResponse,
    CorporateDealUpdate,
    CorporateDealWinRequest,
)
from services.corporate_service.schemas.employee import (
    CorporateProgramEmployeeResponse,
    EmployeeBulkAddRequest,
    EmployeeBulkAddResponse,
    EmployeeRow,
    MatchMembersResponse,
)
from services.corporate_service.schemas.program import (
    CorporateProgramCreate,
    CorporateProgramListResponse,
    CorporateProgramResponse,
    CorporateProgramUpdate,
    EnrollAllResponse,
    LinkCohortRequest,
    ProvisionWalletRequest,
)
from services.corporate_service.schemas.outreach import (
    OutreachCycleResult,
    OutreachPreviewResponse,
    OutreachSendResult,
    OutreachStartRequest,
    OutreachStateResponse,
)
from services.corporate_service.schemas.portal import (
    PortalEmployeeRow,
    PortalProgramSummary,
    RequestMagicLinkRequest,
    RequestMagicLinkResponse,
    VerifyMagicLinkRequest,
    VerifyMagicLinkResponse,
)
from services.corporate_service.schemas.report import (
    EmailReportRequest,
    EmailReportResponse,
    EmployeeReportRow,
    ProgramOutcomeReportResponse,
)
from services.corporate_service.schemas.touchpoint import (
    CorporateTouchpointCreate,
    CorporateTouchpointResponse,
)

__all__ = [
    # contact
    "CorporateContactCreate",
    "CorporateContactListResponse",
    "CorporateContactResponse",
    "CorporateContactUpdate",
    "PublicLeadCreate",
    "PublicLeadResponse",
    # deal
    "CorporateDealCreate",
    "CorporateDealListResponse",
    "CorporateDealLossRequest",
    "CorporateDealResponse",
    "CorporateDealUpdate",
    "CorporateDealWinRequest",
    # program
    "CorporateProgramCreate",
    "CorporateProgramListResponse",
    "CorporateProgramResponse",
    "CorporateProgramUpdate",
    "EnrollAllResponse",
    "LinkCohortRequest",
    "ProvisionWalletRequest",
    # employee
    "CorporateProgramEmployeeResponse",
    "EmployeeBulkAddRequest",
    "EmployeeBulkAddResponse",
    "EmployeeRow",
    "MatchMembersResponse",
    # touchpoint
    "CorporateTouchpointCreate",
    "CorporateTouchpointResponse",
    # report
    "EmailReportRequest",
    "EmailReportResponse",
    "EmployeeReportRow",
    "ProgramOutcomeReportResponse",
    # portal
    "PortalEmployeeRow",
    "PortalProgramSummary",
    "RequestMagicLinkRequest",
    "RequestMagicLinkResponse",
    "VerifyMagicLinkRequest",
    "VerifyMagicLinkResponse",
    # outreach
    "OutreachCycleResult",
    "OutreachPreviewResponse",
    "OutreachSendResult",
    "OutreachStartRequest",
    "OutreachStateResponse",
]
