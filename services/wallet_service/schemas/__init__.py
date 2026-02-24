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
]
