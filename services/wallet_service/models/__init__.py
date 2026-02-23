"""Wallet Service models package.

Re-exports all models and enums so that:
  - ``from services.wallet_service.models import Wallet`` works unchanged
  - Alembic env.py imports continue to work without modification
  - SQLAlchemy's mapper registry sees every model class on import

IMPORTANT: Every model class AND enum must be listed here.
When adding a new model, add both its import and its __all__ entry.
"""

# Phase 5 — Corporate wallet stubs
from services.wallet_service.models.corporate import (  # noqa: F401
    CorporateWallet,
    CorporateWalletMember,
)

# Enums
from services.wallet_service.models.enums import (  # noqa: F401
    AuditAction,
    GrantType,
    PaymentMethod,
    ReferralStatus,
    TopupStatus,
    TransactionDirection,
    TransactionStatus,
    TransactionType,
    WalletStatus,
    WalletTier,
)

# Phase 4 — Family wallet stub
from services.wallet_service.models.family import FamilyWalletLink  # noqa: F401
from services.wallet_service.models.grant import (  # noqa: F401
    PromotionalBubbleGrant,
    WalletAuditLog,
)

# Phase 3 — Referrals & Rewards stubs
from services.wallet_service.models.referral import (  # noqa: F401
    ReferralCode,
    ReferralRecord,
)
from services.wallet_service.models.rewards import (  # noqa: F401
    MemberRewardHistory,
    RewardRule,
    WalletEvent,
)
from services.wallet_service.models.topup import WalletTopup  # noqa: F401
from services.wallet_service.models.transaction import WalletTransaction  # noqa: F401

# Phase 1 — Core wallet models
from services.wallet_service.models.wallet import Wallet  # noqa: F401

__all__ = [
    # Enums
    "AuditAction",
    "GrantType",
    "PaymentMethod",
    "ReferralStatus",
    "TopupStatus",
    "TransactionDirection",
    "TransactionStatus",
    "TransactionType",
    "WalletStatus",
    "WalletTier",
    # Phase 1
    "Wallet",
    "WalletTransaction",
    "WalletTopup",
    "PromotionalBubbleGrant",
    "WalletAuditLog",
    # Phase 3
    "ReferralCode",
    "ReferralRecord",
    "RewardRule",
    "WalletEvent",
    "MemberRewardHistory",
    # Phase 4
    "FamilyWalletLink",
    # Phase 5
    "CorporateWallet",
    "CorporateWalletMember",
]
