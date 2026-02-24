"""SQLAlchemy models for the AI Service."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class AIRequest(Base):
    """Logs every AI provider call for observability and cost tracking."""

    __tablename__ = "ai_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # e.g., "cohort_complexity", "coach_grade"
    model_provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # e.g., "openai", "anthropic"
    model_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g., "gpt-4o", "claude-3-5-sonnet"

    input_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # "pending", "success", "error"
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Performance + cost tracking
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Who requested
    requested_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    requesting_service: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # e.g., "academy_service"

    # Langfuse trace link
    langfuse_trace_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<AIRequest {self.request_type} {self.model_name} {self.status}>"


class AIPromptTemplate(Base):
    """Versioned prompt templates for AI scoring tasks."""

    __tablename__ = "ai_prompt_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )  # e.g., "cohort_complexity_scorer"
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )

    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True
    )  # Expected JSON output structure

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<AIPromptTemplate {self.name} v{self.version}>"


class AIModelConfig(Base):
    """Configuration for AI model providers."""

    __tablename__ = "ai_model_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "openai", "anthropic", "google"
    model_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # "gpt-4o", "claude-3-5-sonnet-20241022"

    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    max_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    temperature: Mapped[float] = mapped_column(Float, default=0.1)

    # Cost tracking (per 1K tokens)
    input_cost_per_1k: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    output_cost_per_1k: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<AIModelConfig {self.provider}/{self.model_name} default={self.is_default}>"
