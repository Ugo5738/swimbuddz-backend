"""Versioned, independent Academy content catalog.

These entities are additive to the legacy programme-bound Milestone,
CurriculumLesson and AcademyQuiz models. No existing student-progress or
commercial records are rewritten by introducing this library.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.common.datetime_utils import utc_now
from libs.db.base import Base


class _CatalogIdentity:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_retired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class _ReleasedVersion:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    created_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AcademyMilestoneDefinition(_CatalogIdentity, Base):
    """Canonical competency identity; rubric and evidence live on versions."""

    __tablename__ = "academy_milestone_definitions"


class AcademyMilestoneVersion(_ReleasedVersion, Base):
    __tablename__ = "academy_milestone_versions"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_amv_definition_version"),
        CheckConstraint("version > 0", name="ck_amv_version_positive"),
        CheckConstraint("state IN ('draft','published')", name="ck_amv_state"),
        CheckConstraint(
            "evidence_policy IN ('video','time_trial','observation')",
            name="ck_amv_evidence_policy",
        ),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_definitions.id"),
        nullable=False,
        index=True,
    )
    rubric_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="video", server_default="video"
    )
    assessment_context_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    safety_gate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    demo_media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class AcademyDrill(_CatalogIdentity, Base):
    __tablename__ = "academy_drills"


class AcademyDrillVersion(_ReleasedVersion, Base):
    __tablename__ = "academy_drill_versions"
    __table_args__ = (
        UniqueConstraint("drill_id", "version", name="uq_adv_drill_version"),
        CheckConstraint("version > 0", name="ck_adv_version_positive"),
        CheckConstraint("state IN ('draft','published')", name="ck_adv_state"),
    )

    drill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academy_drills.id"), nullable=False, index=True
    )
    steps_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    safety_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    primary_focal_cue: Mapped[str] = mapped_column(Text, nullable=False)
    requires_supervision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    demo_media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


class AcademyLessonBlueprint(_CatalogIdentity, Base):
    __tablename__ = "academy_lesson_blueprints"


class AcademyLessonVersion(_ReleasedVersion, Base):
    __tablename__ = "academy_lesson_versions"
    __table_args__ = (
        UniqueConstraint("lesson_id", "version", name="uq_alv_lesson_version"),
        CheckConstraint("version > 0", name="ck_alv_version_positive"),
        CheckConstraint("state IN ('draft','published')", name="ck_alv_state"),
        CheckConstraint("duration_minutes > 0", name="ck_alv_duration_positive"),
    )

    lesson_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_lesson_blueprints.id"),
        nullable=False,
        index=True,
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    blocks_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    safety_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class AcademyKnowledgeQuiz(_CatalogIdentity, Base):
    __tablename__ = "academy_knowledge_quizzes"


class AcademyKnowledgeQuizVersion(_ReleasedVersion, Base):
    __tablename__ = "academy_knowledge_quiz_versions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "version", name="uq_akqv_quiz_version"),
        CheckConstraint("version > 0", name="ck_akqv_version_positive"),
        CheckConstraint("state IN ('draft','published')", name="ck_akqv_state"),
        CheckConstraint(
            "passing_percent BETWEEN 1 AND 100", name="ck_akqv_passing_percent"
        ),
    )

    quiz_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_knowledge_quizzes.id"),
        nullable=False,
        index=True,
    )
    questions_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    passing_percent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )


class AcademyModule(_CatalogIdentity, Base):
    __tablename__ = "academy_modules"


class AcademyModuleVersion(_ReleasedVersion, Base):
    __tablename__ = "academy_module_versions"
    __table_args__ = (
        UniqueConstraint("module_id", "version", name="uq_amo_module_version"),
        CheckConstraint("version > 0", name="ck_amo_version_positive"),
        CheckConstraint("state IN ('draft','published')", name="ck_amo_state"),
    )

    module_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_modules.id"),
        nullable=False,
        index=True,
    )
    outcomes_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    completion_policy_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )


class AcademyLessonDrill(Base):
    __tablename__ = "academy_lesson_drills"
    __table_args__ = (
        UniqueConstraint(
            "lesson_version_id", "drill_version_id", name="uq_ald_lesson_drill"
        ),
        UniqueConstraint(
            "lesson_version_id", "order_index", name="uq_ald_lesson_order"
        ),
        CheckConstraint("sets > 0", name="ck_ald_sets_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    lesson_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_lesson_versions.id"),
        nullable=False,
        index=True,
    )
    drill_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_drill_versions.id"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    sets: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    distance_m: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    focal_cue_override: Mapped[str | None] = mapped_column(Text, nullable=True)


class AcademyModuleLesson(Base):
    __tablename__ = "academy_module_lessons"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id", "lesson_version_id", name="uq_aml_module_lesson"
        ),
        UniqueConstraint(
            "module_version_id", "order_index", name="uq_aml_module_order"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_module_versions.id"),
        nullable=False,
        index=True,
    )
    lesson_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_lesson_versions.id"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class AcademyModuleMilestone(Base):
    __tablename__ = "academy_module_milestones"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id", "milestone_version_id",
            name="uq_amm_module_milestone",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_module_versions.id"),
        nullable=False,
        index=True,
    )
    milestone_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_versions.id"),
        nullable=False,
        index=True,
    )
    required_for_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AcademyModuleQuiz(Base):
    __tablename__ = "academy_module_quizzes"
    __table_args__ = (
        UniqueConstraint(
            "module_version_id", "quiz_version_id", name="uq_amq_module_quiz"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_module_versions.id"),
        nullable=False,
        index=True,
    )
    quiz_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_knowledge_quiz_versions.id"),
        nullable=False,
        index=True,
    )
    required_for_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class AcademyProgramModule(Base):
    __tablename__ = "academy_program_modules"
    __table_args__ = (
        UniqueConstraint("program_id", "module_version_id", name="uq_apm_program_module"),
        UniqueConstraint("program_id", "order_index", name="uq_apm_program_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True
    )
    module_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_module_versions.id"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)


class AcademyProgramMilestoneAssignment(Base):
    """Version-pinned optional direct requirement, independent of modules."""

    __tablename__ = "academy_program_milestone_assignments"
    __table_args__ = (
        UniqueConstraint(
            "program_id", "milestone_version_id", name="uq_apma_program_milestone"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    program_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("programs.id"), nullable=False, index=True
    )
    milestone_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_versions.id"),
        nullable=False,
        index=True,
    )
    required_for_completion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    target_week: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AcademyCohortMilestoneAssignment(Base):
    """Cohort-specific additions; cannot remove canonical safety requirements."""

    __tablename__ = "academy_cohort_milestone_assignments"
    __table_args__ = (
        UniqueConstraint(
            "cohort_id", "milestone_version_id", name="uq_acma_cohort_milestone"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cohort_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cohorts.id"), nullable=False, index=True
    )
    milestone_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_versions.id"),
        nullable=False,
        index=True,
    )
    target_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_by_auth_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AcademyMilestoneSkill(Base):
    __tablename__ = "academy_milestone_skills"
    __table_args__ = (
        UniqueConstraint("milestone_id", "skill_id", name="uq_ams_milestone_skill"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    milestone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_milestone_definitions.id"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id"), nullable=False, index=True
    )


class AcademyDrillSkill(Base):
    __tablename__ = "academy_drill_skills"
    __table_args__ = (
        UniqueConstraint("drill_id", "skill_id", name="uq_ads_drill_skill"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    drill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("academy_drills.id"),
        nullable=False,
        index=True,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("skills.id"), nullable=False, index=True
    )
