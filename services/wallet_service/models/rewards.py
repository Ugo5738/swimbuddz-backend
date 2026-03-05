"""Phase 3b — Rewards engine models."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.wallet_service.models.enums import (
    AlertSeverity,
    AlertStatus,
    RewardCategory,
    RewardPeriod,
    enum_values,
)


class RewardRule(Base):
    """Admin-configurable rules defining when Bubbles are auto-granted."""

    __tablename__ = "reward_rules"
    __table_args__ = (
        Index("ix_reward_rules_event_type_active", "event_type", "is_active"),
        CheckConstraint("reward_bubbles > 0", name="ck_reward_rules_positive_bubbles"),
        {},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rule_name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    trigger_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reward_bubbles: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_description_template: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    max_per_member_lifetime: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    max_per_member_per_period: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    period: Mapped[Optional[RewardPeriod]] = mapped_column(
        SAEnum(RewardPeriod, name="reward_period_enum", values_callable=enum_values),
        nullable=True,
    )
    replaces_rule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    category: Mapped[RewardCategory] = mapped_column(
        SAEnum(
            RewardCategory, name="reward_category_enum", values_callable=enum_values
        ),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requires_admin_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def render_description(self, event_data: dict) -> str:
        """Render the reward description template with event data."""
        if not self.reward_description_template:
            return f"Reward — {self.display_name} ({self.reward_bubbles} 🫧)"
        try:
            return self.reward_description_template.format(
                amount=self.reward_bubbles, **event_data
            )
        except (KeyError, IndexError):
            return f"Reward — {self.display_name} ({self.reward_bubbles} 🫧)"

    def __repr__(self) -> str:
        return f"<RewardRule {self.rule_name}>"


class WalletEvent(Base):
    """Ingested events from all services for rewards engine."""

    __tablename__ = "wallet_events"
    __table_args__ = (
        Index("ix_wallet_events_processed_created", "processed", "created_at"),
        {},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    member_auth_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    service_source: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rewards_granted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<WalletEvent {self.event_type} {self.member_auth_id}>"


class MemberRewardHistory(Base):
    """Tracks which rewards each member received (for cap enforcement)."""

    __tablename__ = "member_reward_history"
    __table_args__ = (
        Index(
            "ix_member_reward_history_cap_check",
            "member_auth_id",
            "reward_rule_id",
            "period_key",
        ),
        {},
    )

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
    period_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<MemberRewardHistory {self.member_auth_id} rule={self.reward_rule_id}>"


class RewardAlert(Base):
    """Anti-abuse monitoring alerts surfaced in the admin dashboard."""

    __tablename__ = "reward_alerts"
    __table_args__ = (
        Index("ix_reward_alerts_status_created", "status", "created_at"),
        {},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    alert_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        SAEnum(
            AlertSeverity,
            name="alert_severity_enum",
            values_callable=enum_values,
        ),
        nullable=False,
    )
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(
            AlertStatus,
            name="alert_status_enum",
            values_callable=enum_values,
        ),
        default=AlertStatus.OPEN,
        nullable=False,
    )
    member_auth_id: Mapped[Optional[str]] = mapped_column(
        String, index=True, nullable=True
    )
    referral_code_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    alert_data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    resolved_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<RewardAlert {self.alert_type} {self.status.value}>"


class RewardNotificationPreference(Base):
    """Per-member notification preferences for reward events."""

    __tablename__ = "reward_notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_auth_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    notify_on_reward: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_on_referral_qualified: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_on_ambassador_milestone: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_on_streak_milestone: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    notify_channel: Mapped[str] = mapped_column(
        String, default="in_app", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def __repr__(self) -> str:
        return f"<RewardNotificationPreference {self.member_auth_id}>"
