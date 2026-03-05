"""Wallet Service schemas package.

Re-exports all schemas so that:
  - ``from services.wallet_service.schemas import WalletResponse`` works unchanged
  - Router files need no import path changes

IMPORTANT: Every schema class must be listed here.
When adding a new schema, add its import and __all__ entry.
"""

from services.wallet_service.schemas.admin import (  # noqa: F401
    AdjustBalanceRequest,
    AdminStatsResponse,
    AdminTopupListResponse,
    AdminTopupResponse,
    AdminTransactionListResponse,
    AdminWalletListResponse,
    AdminWalletResponse,
    AuditLogEntry,
    AuditLogListResponse,
    FreezeWalletRequest,
    MemberIdentityResponse,
    UnfreezeWalletRequest,
)
from services.wallet_service.schemas.balance import (  # noqa: F401
    BalanceCheckRequest,
    BalanceCheckResponse,
    BalanceResponse,
)
from services.wallet_service.schemas.grant import (  # noqa: F401
    AdminScholarshipCreditRequest,
    BulkGrantPromotionalRequest,
    GrantListResponse,
    GrantPromotionalRequest,
    GrantResponse,
    GrantWelcomeBonusRequest,
    GrantWelcomeBonusResponse,
)
from services.wallet_service.schemas.referral import (  # noqa: F401
    AdminReferralListResponse,
    AdminReferralProgramStats,
    ReferralApplyRequest,
    ReferralApplyResponse,
    ReferralCodeResponse,
    ReferralCodeValidateResponse,
    ReferralHistoryItem,
    ReferralStatsResponse,
)
from services.wallet_service.schemas.rewards import (  # noqa: F401
    AdminEventSubmitRequest,
    AlertSummaryItem,
    AmbassadorStatusResponse,
    EventIngestRequest,
    EventIngestResponse,
    EventTypeCount,
    LeaderboardEntry,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    ReferralLeaderboardResponse,
    RewardAlertListResponse,
    RewardAlertResponse,
    RewardAlertSummaryResponse,
    RewardAlertUpdateRequest,
    RewardAnalyticsResponse,
    RewardCategoryStats,
    RewardEventListItem,
    RewardEventListResponse,
    RewardGrantItem,
    RewardRuleCreateRequest,
    RewardRuleDetailResponse,
    RewardRuleListResponse,
    RewardRuleResponse,
    RewardRuleUpdateRequest,
    RewardStatsResponse,
    TopRuleUsage,
)
from services.wallet_service.schemas.topup import (  # noqa: F401
    ConfirmTopupRequest,
    TopupInitiateRequest,
    TopupListResponse,
    TopupResponse,
)
from services.wallet_service.schemas.transaction import (  # noqa: F401
    CreditRequest,
    DebitRequest,
    InternalDebitCreditResponse,
    TransactionListResponse,
    TransactionResponse,
)
from services.wallet_service.schemas.wallet import (  # noqa: F401
    WalletCreateRequest,
    WalletResponse,
)

__all__ = [
    # Wallet
    "WalletCreateRequest",
    "WalletResponse",
    # Transaction
    "CreditRequest",
    "DebitRequest",
    "InternalDebitCreditResponse",
    "TransactionListResponse",
    "TransactionResponse",
    # Topup
    "ConfirmTopupRequest",
    "TopupInitiateRequest",
    "TopupListResponse",
    "TopupResponse",
    # Balance
    "BalanceCheckRequest",
    "BalanceCheckResponse",
    "BalanceResponse",
    # Grant
    "AdminScholarshipCreditRequest",
    "BulkGrantPromotionalRequest",
    "GrantListResponse",
    "GrantPromotionalRequest",
    "GrantResponse",
    "GrantWelcomeBonusRequest",
    "GrantWelcomeBonusResponse",
    # Admin
    "AdjustBalanceRequest",
    "AdminStatsResponse",
    "AdminTopupListResponse",
    "AdminTopupResponse",
    "AdminTransactionListResponse",
    "AdminWalletListResponse",
    "AdminWalletResponse",
    "AuditLogEntry",
    "AuditLogListResponse",
    "FreezeWalletRequest",
    "MemberIdentityResponse",
    "UnfreezeWalletRequest",
    # Referral
    "AdminReferralListResponse",
    "AdminReferralProgramStats",
    "ReferralApplyRequest",
    "ReferralApplyResponse",
    "ReferralCodeResponse",
    "ReferralCodeValidateResponse",
    "ReferralHistoryItem",
    "ReferralStatsResponse",
    # Rewards
    "AdminEventSubmitRequest",
    "EventIngestRequest",
    "EventIngestResponse",
    "EventTypeCount",
    "RewardEventListItem",
    "RewardEventListResponse",
    "RewardGrantItem",
    "RewardRuleCreateRequest",
    "RewardRuleDetailResponse",
    "RewardRuleListResponse",
    "RewardRuleResponse",
    "RewardRuleUpdateRequest",
    "RewardStatsResponse",
    "TopRuleUsage",
    # Phase 3d — Alerts
    "AlertSummaryItem",
    "RewardAlertListResponse",
    "RewardAlertResponse",
    "RewardAlertSummaryResponse",
    "RewardAlertUpdateRequest",
    # Phase 3d — Ambassador
    "AmbassadorStatusResponse",
    # Phase 3d — Leaderboard
    "LeaderboardEntry",
    "ReferralLeaderboardResponse",
    # Phase 3d — Notification Preferences
    "NotificationPreferenceResponse",
    "NotificationPreferenceUpdateRequest",
    # Phase 3d — Analytics
    "RewardAnalyticsResponse",
    "RewardCategoryStats",
]
