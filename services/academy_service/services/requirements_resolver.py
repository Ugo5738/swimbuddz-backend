"""Build a deterministic cohort-specific snapshot from published catalog releases.

The resolver is read-only. It does not award mastery, update enrollment progress,
or modify legacy Academy graduation. Every result is version-pinned.
"""

import hashlib
import json
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.academy_service.models import (
    AcademyCohortMilestoneAssignment,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    AcademyModuleMilestone,
    AcademyModuleVersion,
    AcademyProgramMilestoneAssignment,
    AcademyProgramModule,
    Cohort,
)


def _validate_version(version: AcademyMilestoneVersion | None) -> None:
    if version is None or version.state != "published" or version.published_at is None:
        raise HTTPException(status_code=409, detail="Assigned milestone version is not published")
    if version.evidence_policy not in {"video", "time_trial", "observation"}:
        raise HTTPException(status_code=409, detail="Assigned milestone evidence policy is invalid")
    if not isinstance(version.rubric_json, dict) or not version.rubric_json:
        raise HTTPException(status_code=409, detail="Assigned milestone lacks a rubric")


async def build_cohort_requirements(
    db: AsyncSession,
    cohort: Cohort,
) -> dict[str, Any]:
    """Resolve modules + program requirements + cohort additions, fail closed.

    A canonical milestone may appear in multiple places, but its published
    version must be identical in all of them. Program-level optionality cannot
    make a safety gate optional. Cohort assignments can move target weeks or
    add a milestone; they cannot replace or remove an existing safety rule.
    """
    module_links = (
        (
            await db.execute(
                select(AcademyProgramModule)
                .where(AcademyProgramModule.program_id == cohort.program_id)
                .order_by(AcademyProgramModule.order_index, AcademyProgramModule.id)
            )
        )
        .scalars()
        .all()
    )

    module_ids = [link.module_version_id for link in module_links]
    module_versions = (
        (
            await db.execute(
                select(AcademyModuleVersion).where(AcademyModuleVersion.id.in_(module_ids))
            )
        )
        .scalars()
        .all()
        if module_ids
        else []
    )
    module_by_id = {module.id: module for module in module_versions}
    if any(
        module_by_id.get(module_id) is None
        or module_by_id[module_id].state != "published"
        or module_by_id[module_id].published_at is None
        for module_id in module_ids
    ):
        raise HTTPException(status_code=409, detail="A program module version is not published")

    module_milestones = (
        (
            await db.execute(
                select(AcademyModuleMilestone).where(
                    AcademyModuleMilestone.module_version_id.in_(module_ids)
                )
            )
        )
        .scalars()
        .all()
        if module_ids
        else []
    )
    program_milestones = (
        (
            await db.execute(
                select(AcademyProgramMilestoneAssignment).where(
                    AcademyProgramMilestoneAssignment.program_id == cohort.program_id
                )
            )
        )
        .scalars()
        .all()
    )
    cohort_milestones = (
        (
            await db.execute(
                select(AcademyCohortMilestoneAssignment).where(
                    AcademyCohortMilestoneAssignment.cohort_id == cohort.id
                )
            )
        )
        .scalars()
        .all()
    )

    milestone_ids = {
        entry.milestone_version_id
        for entry in [*module_milestones, *program_milestones, *cohort_milestones]
    }
    versions = (
        (
            await db.execute(
                select(AcademyMilestoneVersion).where(
                    AcademyMilestoneVersion.id.in_(milestone_ids)
                )
            )
        )
        .scalars()
        .all()
        if milestone_ids
        else []
    )
    version_by_id = {version.id: version for version in versions}
    for version_id in milestone_ids:
        _validate_version(version_by_id.get(version_id))

    definitions = (
        (
            await db.execute(
                select(AcademyMilestoneDefinition).where(
                    AcademyMilestoneDefinition.id.in_(
                        {version.definition_id for version in versions}
                    )
                )
            )
        )
        .scalars()
        .all()
        if versions
        else []
    )
    definition_by_id = {item.id: item for item in definitions}
    if len(definition_by_id) != len({version.definition_id for version in versions}):
        raise HTTPException(status_code=409, detail="Missing canonical milestone definition")

    resolved: dict[uuid.UUID, dict[str, Any]] = {}

    def add(
        version_id: uuid.UUID,
        *,
        source: str,
        required: bool,
        target_week: int | None,
    ) -> None:
        version = version_by_id[version_id]
        definition = definition_by_id[version.definition_id]
        if target_week is not None and target_week < 1:
            raise HTTPException(status_code=409, detail="Milestone target week must be positive")
        previous = resolved.get(definition.id)
        if previous is not None and previous["version_id"] != str(version.id):
            raise HTTPException(
                status_code=409,
                detail=f"Conflicting published versions for {definition.code}",
            )
        if previous is None:
            previous = {
                "definition_id": str(definition.id),
                "code": definition.code,
                "title": definition.title,
                "version_id": str(version.id),
                "version_number": version.version,
                "rubric_snapshot": version.rubric_json,
                "evidence_policy": version.evidence_policy,
                "assessment_context_snapshot": version.assessment_context_json,
                "safety_gate": bool(version.safety_gate),
                "required_for_completion": bool(required or version.safety_gate),
                "target_week": target_week,
                "sources": [],
            }
            resolved[definition.id] = previous
        else:
            previous["required_for_completion"] = bool(
                previous["required_for_completion"] or required or version.safety_gate
            )
            if target_week is not None:
                previous["target_week"] = target_week
        if source not in previous["sources"]:
            previous["sources"].append(source)

    for link in sorted(module_links, key=lambda x: (x.order_index, str(x.id))):
        requirements = sorted(
            (r for r in module_milestones if r.module_version_id == link.module_version_id),
            key=lambda r: (r.order_index, str(r.milestone_version_id)),
        )
        for req in requirements:
            add(
                req.milestone_version_id,
                source=f"module:{link.module_version_id}",
                required=req.required_for_completion,
                target_week=None,
            )

    for assignment in sorted(program_milestones, key=lambda x: str(x.milestone_version_id)):
        add(
            assignment.milestone_version_id,
            source="program",
            required=assignment.required_for_completion,
            target_week=assignment.target_week,
        )

    for assignment in sorted(cohort_milestones, key=lambda x: str(x.milestone_version_id)):
        if not assignment.override_reason.strip():
            raise HTTPException(status_code=409, detail="Cohort milestone needs an override reason")
        add(
            assignment.milestone_version_id,
            source="cohort",
            required=True,
            target_week=assignment.target_week,
        )

    if not resolved:
        raise HTTPException(
            status_code=409, detail="No published requirements assigned to this cohort"
        )

    milestones = sorted(resolved.values(), key=lambda x: (x["code"], x["version_id"]))
    for milestone in milestones:
        milestone["sources"].sort()
    return {
        "schema_version": 1,
        "cohort_id": str(cohort.id),
        "program_id": str(cohort.program_id),
        "milestones": milestones,
    }


def requirements_sha256(requirements: dict[str, Any]) -> str:
    encoded = json.dumps(
        requirements, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
