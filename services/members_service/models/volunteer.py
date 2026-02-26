"""Volunteer and club challenge models."""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class VolunteerRole(Base):
    """LEGACY: Volunteer roles — migrated to volunteer_service.

    Table renamed to legacy_volunteer_roles. Kept here temporarily so
    the data migration script can read from it. Will be removed after
    prod migration is confirmed.
    """

    __tablename__ = "legacy_volunteer_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # media/logistics/admin/coaching_support/lane_marshal
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    slots_available: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<VolunteerRole {self.title}>"


class VolunteerInterest(Base):
    """LEGACY: Volunteer interests — migrated to volunteer_service.

    Table renamed to legacy_volunteer_interests. Kept here temporarily so
    the data migration script can read from it. Will be removed after
    prod migration is confirmed.
    """

    __tablename__ = "legacy_volunteer_interests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="interested"
    )  # interested/active/inactive
    notes: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<VolunteerInterest member={self.member_id} role={self.role_id}>"


class ClubChallenge(Base):
    """Club challenges that members can complete to earn badges."""

    __tablename__ = "club_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    challenge_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # time_trial/attendance/distance/technique
    badge_name: Mapped[str] = mapped_column(String, nullable=False)
    criteria_json: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<ClubChallenge {self.title}>"


class MemberChallengeCompletion(Base):
    """Tracks member completion of club challenges."""

    __tablename__ = "member_challenge_completions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    challenge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    result_data: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<MemberChallengeCompletion member={self.member_id} challenge={self.challenge_id}>"
