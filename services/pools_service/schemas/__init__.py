"""Pools service schemas package."""

from services.pools_service.schemas.main import (
    PoolCreate,
    PoolListResponse,
    PoolResponse,
    PoolUpdate,
)
from services.pools_service.schemas.related import (
    PoolAgreementCreate,
    PoolAgreementResponse,
    PoolAgreementUpdate,
    PoolAssetCreate,
    PoolAssetResponse,
    PoolAssetUpdate,
    PoolContactCreate,
    PoolContactResponse,
    PoolContactUpdate,
    PoolStatusChangeResponse,
    PoolVisitCreate,
    PoolVisitResponse,
    PoolVisitUpdate,
)
from services.pools_service.schemas.submission import (
    PoolSubmissionApproveRequest,
    PoolSubmissionCreate,
    PoolSubmissionListResponse,
    PoolSubmissionRejectRequest,
    PoolSubmissionResponse,
)

__all__ = [
    # main
    "PoolCreate",
    "PoolListResponse",
    "PoolResponse",
    "PoolUpdate",
    # submissions
    "PoolSubmissionApproveRequest",
    "PoolSubmissionCreate",
    "PoolSubmissionListResponse",
    "PoolSubmissionRejectRequest",
    "PoolSubmissionResponse",
    # contacts
    "PoolContactCreate",
    "PoolContactResponse",
    "PoolContactUpdate",
    # visits
    "PoolVisitCreate",
    "PoolVisitResponse",
    "PoolVisitUpdate",
    # status history
    "PoolStatusChangeResponse",
    # agreements
    "PoolAgreementCreate",
    "PoolAgreementResponse",
    "PoolAgreementUpdate",
    # assets
    "PoolAssetCreate",
    "PoolAssetResponse",
    "PoolAssetUpdate",
]
