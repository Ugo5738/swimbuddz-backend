"""Volunteer (legacy) and club challenge models.

The VolunteerRole / VolunteerInterest tables were migrated to volunteer_service
and remain here only as legacy stubs. The challenges system lives here and was
expanded in Phase 1 of the challenges revamp to support:

  * Audience scoping (community / club / academy / all) plus optional
    per-instance scoping to a specific club_id or academy_cohort_id.
  * Challenge format: participatory (anyone who completes earns the badge)
    vs competition (admin marks one submission as the winner).
  * Configurable rewards: badge name + optional badge image, optional
    Bubbles credits, optional volunteer hours.
  * Multiple example media per challenge.
  * Multiple proof media per submission.
  * Solo or team submissions (with min/max team size).
  * A submission lifecycle: pending → approved | rejected, multiple
    attempts allowed per (member, challenge), prior attempts preserved.
  * A separate badge-awards table for fast member-profile lookup.

Cross-service references (media_id, club_id, academy_cohort_id, badge image,
bubbles grant id, volunteer hours log id) are stored as plain UUIDs without
hard foreign keys — services own their own data.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
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


# ---------------------------------------------------------------------------
# Club challenges
# ---------------------------------------------------------------------------

# Audience values (string column, no enum to keep migrations lightweight)
CHALLENGE_AUDIENCES = ("community", "club", "academy", "all")
CHALLENGE_FORMATS = ("participatory", "competition")
CHALLENGE_TYPES = ("time_trial", "attendance", "distance", "technique")
SUBMISSION_STATUSES = ("pending", "approved", "rejected")


class ClubChallenge(Base):
    """A challenge that members can attempt to earn a badge (and optional rewards).

    See module docstring for the conceptual model. Audience controls which
    membership tier sees the challenge; club_id / academy_cohort_id optionally
    narrow to a specific club or cohort. format='participatory' means anyone
    who is approved earns the badge; format='competition' means admin must
    additionally pick a winner_submission_id.
    """

    __tablename__ = "club_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    instructions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # stringified BlockNote PartialBlock[] JSON; mirrors ContentPost.body

    challenge_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # time_trial / attendance / distance / technique
    badge_name: Mapped[str] = mapped_column(String, nullable=False)
    reward_badge_image_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # ref to media_service; optional badge artwork

    # Configurable rewards (creator opts in by setting non-null)
    reward_bubbles_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reward_volunteer_hours: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    criteria_json: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # legacy; freeform JSON for type-specific criteria

    # Audience + scoping
    audience: Mapped[str] = mapped_column(
        String, nullable=False, default="all", server_default="all"
    )  # community / club / academy / all
    club_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # optional per-instance scope; cross-service ref
    academy_cohort_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # optional per-instance scope; cross-service ref

    # Format + winner (competition only)
    format: Mapped[str] = mapped_column(
        String, nullable=False, default="participatory", server_default="participatory"
    )  # participatory / competition
    winner_submission_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # references member_challenge_completions.id; soft FK to avoid circular cascade

    # Skill-ladder series (Phase B of the challenges revamp).
    #
    # When `series_slug` is set, this challenge is a step inside an ordered
    # ladder rather than a one-off — a Club skill curriculum, an open-water
    # progression, etc. Examples: "club-fundamentals", "open-water".
    #
    #   series_slug   = which ladder this belongs to (NULL = standalone)
    #   series_order  = position in the ladder (1, 2, 3, ...)
    #   requires_challenge_id = OPTIONAL hard-gating: members must have an
    #                           approved badge for this prerequisite before
    #                           they can submit. Soft progression (no
    #                           requires_challenge_id) is the default — the
    #                           ladder is guidance, not enforcement.
    #
    # See docs/club/POD_OPERATIONS.md and the challenges revamp plan.
    series_slug: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    series_order: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requires_challenge_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # soft FK to club_challenges.id; no DB-level FK to avoid circular cascade

    # Visibility
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )  # opt-in to the public landing-page surface
    show_winner_media_publicly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )  # whether to show the winner's submission media on the public page
    starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Team support
    team_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    team_min_size: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    team_max_size: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self):
        return f"<ClubChallenge {self.title}>"


class ChallengeExampleMedia(Base):
    """Multiple example media items shown on the challenge detail/listing.

    media_id references media_service; no hard FK across services.
    """

    __tablename__ = "challenge_example_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("club_challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    caption: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<ChallengeExampleMedia challenge={self.challenge_id} media={self.media_id}>"


class MemberChallengeCompletion(Base):
    """A member (or team) submission for a challenge.

    Submissions have a lifecycle: pending → approved | rejected. Multiple
    submissions per (member, challenge) are allowed; prior attempts are
    preserved (no deletes/overwrites). For team submissions,
    submitted_by_member_id is the captain; the full team roster lives in
    challenge_submission_members.

    Legacy column `verified_by` is retained for backwards compatibility but
    new flows write `reviewed_by` instead.
    """

    __tablename__ = "member_challenge_completions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    challenge_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Submitter (captain for team subs); for solo subs equals member_id
    submitted_by_member_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Submission content
    submission_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_team_submission: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )  # pending / approved / rejected
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reviewed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # admin auth UUID who reviewed
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Reward distribution timestamp (per-member ledger lives in challenge_submission_members)
    rewards_distributed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Legacy / Phase-0
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    result_data: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verified_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # legacy; replaced by reviewed_by — kept to avoid destructive rename

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return (
            f"<MemberChallengeCompletion member={self.member_id} "
            f"challenge={self.challenge_id} status={self.status}>"
        )


class ChallengeSubmissionMedia(Base):
    """Proof media (photos/videos) attached to a submission."""

    __tablename__ = "challenge_submission_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member_challenge_completions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<ChallengeSubmissionMedia submission={self.submission_id} media={self.media_id}>"


class ChallengeSubmissionMember(Base):
    """Per-member roster on a submission (team or solo).

    For solo submissions, exactly one row is created. For team submissions,
    one row per teammate (including the captain). Reward grants are tracked
    per-member here so re-approval and per-member retries are clean:
      - bubbles_grant_id: id of the wallet_service grant for this member
      - volunteer_hours_log_id: id of the volunteer_service hours log row
      - badge_awarded: local flag once a row in challenge_badge_awards exists

    See cross-service approval flow in the Phase 7 plan.
    """

    __tablename__ = "challenge_submission_members"
    __table_args__ = (
        UniqueConstraint("submission_id", "member_id", name="uq_submission_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member_challenge_completions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    role: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # e.g. "captain" for the submitter on team subs

    # Per-member reward ledger (populated on approval)
    badge_awarded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    bubbles_grant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # wallet_service grant id; idempotency via wallet's campaign_code
    volunteer_hours_log_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )  # volunteer_service hours-log row id
    rewarded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return (
            f"<ChallengeSubmissionMember submission={self.submission_id} "
            f"member={self.member_id}>"
        )


class ChallengeBadgeAward(Base):
    """Denormalised badge ledger for the member profile.

    One row per (member, challenge) once a badge is awarded. Lets the profile
    page do a single indexed lookup by member_id without scanning the larger
    member_challenge_completions table.
    """

    __tablename__ = "challenge_badge_awards"
    __table_args__ = (
        UniqueConstraint("member_id", "challenge_id", name="uq_badge_member_challenge"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member_challenge_completions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Snapshot at time of award (denormalised so badge survives challenge edits)
    badge_name: Mapped[str] = mapped_column(String, nullable=False)
    badge_image_media_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    def __repr__(self):
        return f"<ChallengeBadgeAward member={self.member_id} challenge={self.challenge_id}>"
