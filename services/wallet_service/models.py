"""SQLAlchemy models for the Wallet Service.

All 13 tables are defined here. Phase 1 tables are fully active;
Phase 3–5 tables are stubs (models only, logic deferred).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Enums (match design doc Section 4)
# ---------------------------------------------------------------------------


class WalletStatus(str, enum.Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class WalletTier(str, enum.Enum):
    STANDARD = "standard"
    PREMIUM = "premium"
    VIP = "vip"


class TransactionType(str, enum.Enum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    REFUND = "refund"
    WELCOME_BONUS = "welcome_bonus"
    PROMOTIONAL_CREDIT = "promotional_credit"
    REFERRAL_CREDIT = "referral_credit"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    PENALTY = "penalty"
    REWARD = "reward"
    EXPIRY = "expiry"


class TransactionDirection(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"


class TopupStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class PaymentMethod(str, enum.Enum):
    PAYSTACK = "paystack"
    BANK_TRANSFER = "bank_transfer"
    ADMIN_GRANT = "admin_grant"


class GrantType(str, enum.Enum):
    WELCOME_BONUS = "welcome_bonus"
    REFERRAL_REWARD = "referral_reward"
    LOYALTY_REWARD = "loyalty_reward"
    CAMPAIGN = "campaign"
    COMPENSATION = "compensation"
    ADMIN_MANUAL = "admin_manual"


class ReferralStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REWARDED = "rewarded"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AuditAction(str, enum.Enum):
    FREEZE = "freeze"
    UNFREEZE = "unfreeze"
    SUSPEND = "suspend"
    CLOSE = "close"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    TIER_CHANGE = "tier_change"
    LIMIT_CHANGE = "limit_change"


# ---------------------------------------------------------------------------
# Phase 1 — Active Tables
# ---------------------------------------------------------------------------


class Wallet(Base):
    """Core wallet account. One per member, created on registration."""

    __tablename__ = "wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, index=True, nullable=False
    )
    member_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lifetime_bubbles_purchased: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    lifetime_bubbles_spent: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    lifetime_bubbles_received: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    status: Mapped[WalletStatus] = mapped_column(
        SAEnum(WalletStatus, name="wallet_status_enum"),
        default=WalletStatus.ACTIVE,
        nullable=False,
    )
    frozen_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    frozen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    frozen_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wallet_tier: Mapped[WalletTier] = mapped_column(
        SAEnum(WalletTier, name="wallet_tier_enum"),
        default=WalletTier.STANDARD,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships (within service)
    transactions: Mapped[list["WalletTransaction"]] = relationship(
        back_populates="wallet", lazy="selectin"
    )
    topups: Mapped[list["WalletTopup"]] = relationship(
        back_populates="wallet", lazy="selectin"
    )
    grants: Mapped[list["PromotionalBubbleGrant"]] = relationship(
        back_populates="wallet", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_wallet_balance_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Wallet {self.id} member_auth_id={self.member_auth_id} balance={self.balance}>"


class WalletTransaction(Base):
    """Immutable ledger of all balance changes. Source of truth."""

    __tablename__ = "wallet_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, name="transaction_type_enum"), nullable=False
    )
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection, name="transaction_direction_enum"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, name="transaction_status_enum"),
        default=TransactionStatus.PENDING,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    service_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reference_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    initiated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    txn_metadata: Mapped[Optional[dict]] = mapped_column(
        "txn_metadata", JSONB, nullable=True
    )
    reversed_by_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reversal_of_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="transactions")

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_transaction_amount_positive"),
        Index(
            "ix_wallet_transactions_wallet_created",
            "wallet_id",
            "created_at",
            postgresql_using="btree",
        ),
    )

    def __repr__(self) -> str:
        return f"<WalletTransaction {self.id} {self.direction.value} {self.amount}>"


class WalletTopup(Base):
    """Tracks Bubble purchase requests and Paystack payment lifecycle."""

    __tablename__ = "wallet_topups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reference: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    bubbles_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    naira_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    exchange_rate: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    payment_reference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    payment_method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod, name="topup_payment_method_enum"), nullable=False
    )
    status: Mapped[TopupStatus] = mapped_column(
        SAEnum(TopupStatus, name="topup_status_enum"),
        default=TopupStatus.PENDING,
        nullable=False,
    )
    paystack_authorization_url: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    paystack_access_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topup_metadata: Mapped[Optional[dict]] = mapped_column(
        "topup_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="topups")

    __table_args__ = (
        CheckConstraint("bubbles_amount >= 25", name="ck_topup_min_bubbles"),
        CheckConstraint("bubbles_amount <= 5000", name="ck_topup_max_bubbles"),
        CheckConstraint("naira_amount > 0", name="ck_topup_naira_positive"),
    )

    def __repr__(self) -> str:
        return (
            f"<WalletTopup {self.id} {self.bubbles_amount} bubbles {self.status.value}>"
        )


class PromotionalBubbleGrant(Base):
    """Tracks promotional/bonus Bubbles issued by admins or system rules."""

    __tablename__ = "promotional_bubble_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wallets.id"), nullable=False, index=True
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    grant_type: Mapped[GrantType] = mapped_column(
        SAEnum(GrantType, name="grant_type_enum"), nullable=False
    )
    bubbles_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    campaign_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bubbles_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    granted_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    grant_metadata: Mapped[Optional[dict]] = mapped_column(
        "grant_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    # Relationship
    wallet: Mapped["Wallet"] = relationship(back_populates="grants")

    __table_args__ = (
        CheckConstraint("bubbles_amount > 0", name="ck_grant_amount_positive"),
        CheckConstraint(
            "bubbles_remaining >= 0", name="ck_grant_remaining_non_negative"
        ),
        CheckConstraint(
            "bubbles_remaining <= bubbles_amount",
            name="ck_grant_remaining_lte_amount",
        ),
    )

    def __repr__(self) -> str:
        return f"<PromotionalBubbleGrant {self.id} {self.grant_type.value} {self.bubbles_amount}>"


class WalletAuditLog(Base):
    """Tracks sensitive admin operations on wallets."""

    __tablename__ = "wallet_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    action: Mapped[AuditAction] = mapped_column(
        SAEnum(AuditAction, name="audit_action_enum"), nullable=False
    )
    performed_by: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<WalletAuditLog {self.id} {self.action.value}>"


# ---------------------------------------------------------------------------
# Phase 3 — Referral & Rewards Stubs (tables created now, logic built later)
# ---------------------------------------------------------------------------


class ReferralCode(Base):
    """Unique shareable referral codes per member."""

    __tablename__ = "referral_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_uses: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=50)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ReferralCode {self.code}>"


class ReferralRecord(Base):
    """Tracks referral lifecycle (pending → qualified → rewarded)."""

    __tablename__ = "referral_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    referrer_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    referee_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    referral_code_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    status: Mapped[ReferralStatus] = mapped_column(
        SAEnum(ReferralStatus, name="referral_status_enum"),
        default=ReferralStatus.PENDING,
        nullable=False,
    )
    referrer_reward_bubbles: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    referee_reward_bubbles: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    referrer_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    referee_transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    qualified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rewarded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ReferralRecord {self.id} {self.status.value}>"


class RewardRule(Base):
    """Admin-configurable rules defining when Bubbles are auto-granted."""

    __tablename__ = "reward_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    condition_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reward_bubbles: Mapped[int] = mapped_column(Integer, nullable=False)
    max_grants_per_member: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_grants_per_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    period_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<RewardRule {self.name}>"


class WalletEvent(Base):
    """Ingested events from all services for rewards engine."""

    __tablename__ = "wallet_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    source_service: Mapped[str] = mapped_column(String, nullable=False)
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<WalletEvent {self.event_type} {self.member_auth_id}>"


class MemberRewardHistory(Base):
    """Tracks which rewards each member received (for cap enforcement)."""

    __tablename__ = "member_reward_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    reward_rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    wallet_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    bubbles_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<MemberRewardHistory {self.member_auth_id} rule={self.reward_rule_id}>"


# ---------------------------------------------------------------------------
# Phase 4 — Family Wallet Stub
# ---------------------------------------------------------------------------


class FamilyWalletLink(Base):
    """Links wallets in a parent-child relationship for family spending."""

    __tablename__ = "family_wallet_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    parent_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    child_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    spending_limit_per_month: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    spent_this_month: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    month_reset_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("parent_wallet_id", "child_wallet_id", name="uq_family_link"),
        CheckConstraint(
            "parent_wallet_id != child_wallet_id",
            name="ck_family_no_self_link",
        ),
    )

    def __repr__(self) -> str:
        return f"<FamilyWalletLink parent={self.parent_wallet_id} child={self.child_wallet_id}>"


# ---------------------------------------------------------------------------
# Phase 5 — Corporate Wallet Stubs
# ---------------------------------------------------------------------------


class CorporateWallet(Base):
    """Corporate wallet for companies funding employee wellness programs."""

    __tablename__ = "corporate_wallets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    company_email: Mapped[str] = mapped_column(String, nullable=False)
    admin_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    budget_total: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    member_bubble_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    corp_metadata: Mapped[Optional[dict]] = mapped_column(
        "corp_metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<CorporateWallet {self.company_name}>"


class CorporateWalletMember(Base):
    """Links corporate wallets to individual member wallets."""

    __tablename__ = "corporate_wallet_members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    corporate_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    member_wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    bubbles_allocated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    added_by: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<CorporateWalletMember corp={self.corporate_wallet_id} member={self.member_wallet_id}>"
