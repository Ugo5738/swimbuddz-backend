"""Regression coverage for reusable Academy requirements and cohort snapshots."""

import uuid

import pytest
from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    AcademyCohortRequirementsSnapshot,
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
    AcademyModule,
    AcademyModuleVersion,
    AcademyProgramMilestoneAssignment,
)
from tests.factories import CohortFactory, ProgramFactory

BASE = "/academy/admin"


def _milestone(
    *,
    code: str,
    definition_id: uuid.UUID | None = None,
    version: int = 1,
    safety_gate: bool = False,
):
    identity = None
    if definition_id is None:
        definition_id = uuid.uuid4()
        identity = AcademyMilestoneDefinition(
            id=definition_id,
            code=code,
            title=code.replace("-", " "),
        )
    release = AcademyMilestoneVersion(
        id=uuid.uuid4(),
        definition_id=definition_id,
        version=version,
        rubric_json={"time_seconds": 30, "observable": True},
        evidence_policy="video",
        assessment_context_json={"supervised": True},
        safety_gate=safety_gate,
        state="published",
        published_at=utc_now(),
        created_by_auth_id="test-admin",
    )
    return identity, release


@pytest.mark.asyncio
@pytest.mark.integration
async def test_reusable_milestone_in_two_programs_and_immutable_cohort_manifest(
    academy_client, db_session
):
    programs = [ProgramFactory.create(), ProgramFactory.create()]
    cohorts = [CohortFactory.create(program_id=p.id) for p in programs]
    definition, version = _milestone(code="MS-TEST-TREAD-30")
    db_session.add_all([*programs, *cohorts, definition, version])
    await db_session.commit()

    for program in programs:
        assigned = await academy_client.post(
            f"{BASE}/programs/{program.id}/curriculum/milestones",
            json={"milestone_version_id": str(version.id), "target_week": 4},
        )
        assert assigned.status_code == 201, assigned.text

    for cohort in cohorts:
        preview = await academy_client.get(
            f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/preview"
        )
        assert preview.status_code == 200, preview.text
        milestone = preview.json()["requirements"]["milestones"][0]
        assert milestone["code"] == definition.code
        assert milestone["target_week"] == 4
        assert milestone["required_for_completion"] is True
        assert milestone["rubric_snapshot"] == version.rubric_json
        assert milestone["evidence_policy"] == "video"

    frozen = await academy_client.post(
        f"{BASE}/cohorts/{cohorts[0].id}/curriculum/requirements/freeze"
    )
    assert frozen.status_code == 201, frozen.text
    before = frozen.json()

    retry = await academy_client.post(
        f"{BASE}/cohorts/{cohorts[0].id}/curriculum/requirements/freeze"
    )
    assert retry.status_code == 201
    assert retry.json()["id"] == before["id"]
    assert retry.json()["sha256"] == before["sha256"]

    preserved = await db_session.scalar(
        select(AcademyCohortRequirementsSnapshot).where(
            AcademyCohortRequirementsSnapshot.cohort_id == cohorts[0].id
        )
    )
    assert preserved.requirements_json == before["requirements"]

    extra_definition, extra_version = _milestone(code="MS-TEST-FLOAT-10")
    db_session.add_all([extra_definition, extra_version])
    await db_session.commit()
    new_assignment = await academy_client.post(
        f"{BASE}/programs/{programs[0].id}/curriculum/milestones",
        json={"milestone_version_id": str(extra_version.id)},
    )
    assert new_assignment.status_code == 201, new_assignment.text
    updated_preview = await academy_client.get(
        f"{BASE}/cohorts/{cohorts[0].id}/curriculum/requirements/preview"
    )
    assert updated_preview.status_code == 200
    assert len(updated_preview.json()["requirements"]["milestones"]) == 2

    frozen_again = await academy_client.get(
        f"{BASE}/cohorts/{cohorts[0].id}/curriculum/requirements/frozen"
    )
    assert frozen_again.status_code == 200
    assert frozen_again.json()["sha256"] == before["sha256"]
    assert len(frozen_again.json()["requirements"]["milestones"]) == 1

    denied_override = await academy_client.post(
        f"{BASE}/cohorts/{cohorts[0].id}/curriculum/milestones",
        json={
            "milestone_version_id": str(extra_version.id),
            "override_reason": "Attempt after freezing the contract",
        },
    )
    assert denied_override.status_code == 409, denied_override.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_module_safety_gate_is_nonoptional_and_published_versions_are_locked(
    academy_client, db_session
):
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    definition, milestone = _milestone(code="MS-TEST-SAFETY", safety_gate=True)
    module = AcademyModule(
        id=uuid.uuid4(), code="MODULE-TEST-SAFETY", title="Safety practice"
    )
    module_version = AcademyModuleVersion(
        id=uuid.uuid4(),
        module_id=module.id,
        version=1,
        state="draft",
        created_by_auth_id="test-admin",
        outcomes_json=["Pass swimming safety verification"],
        completion_policy_json={},
    )
    db_session.add_all([program, cohort, definition, milestone, module, module_version])
    await db_session.commit()

    unsafe = await academy_client.post(
        f"{BASE}/modules/{module_version.id}/curriculum/milestones",
        json={
            "milestone_version_id": str(milestone.id),
            "required_for_completion": False,
        },
    )
    assert unsafe.status_code == 409, unsafe.text

    assigned = await academy_client.post(
        f"{BASE}/modules/{module_version.id}/curriculum/milestones",
        json={
            "milestone_version_id": str(milestone.id),
            "required_for_completion": True,
        },
    )
    assert assigned.status_code == 201, assigned.text

    publish = await academy_client.post(
        f"/academy/admin/curriculum-library/module/{module.id}"
        f"/versions/{module_version.id}/publish"
    )
    assert publish.status_code == 200, publish.text

    after_publish = await academy_client.post(
        f"{BASE}/modules/{module_version.id}/curriculum/milestones",
        json={"milestone_version_id": str(milestone.id)},
    )
    assert after_publish.status_code == 409, after_publish.text

    program_module = await academy_client.post(
        f"{BASE}/programs/{program.id}/curriculum/modules",
        json={"module_version_id": str(module_version.id), "order_index": 0},
    )
    assert program_module.status_code == 201, program_module.text

    preview = await academy_client.get(
        f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/preview"
    )
    assert preview.status_code == 200, preview.text
    requirement = preview.json()["requirements"]["milestones"][0]
    assert requirement["safety_gate"] is True
    assert requirement["required_for_completion"] is True
    assert requirement["sources"] == [f"module:{module_version.id}"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_conflicting_released_versions_of_same_competency_fail_closed(
    academy_client, db_session
):
    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    definition, first = _milestone(code="MS-TEST-CONFLICT")
    _, second = _milestone(
        code="MS-TEST-CONFLICT", definition_id=definition.id, version=2
    )
    db_session.add_all([program, cohort, definition, first, second])
    await db_session.commit()

    direct = await academy_client.post(
        f"{BASE}/programs/{program.id}/curriculum/milestones",
        json={"milestone_version_id": str(first.id)},
    )
    assert direct.status_code == 201, direct.text
    cohort_added = await academy_client.post(
        f"{BASE}/cohorts/{cohort.id}/curriculum/milestones",
        json={
            "milestone_version_id": str(second.id),
            "override_reason": "Test conflicting release standard",
        },
    )
    assert cohort_added.status_code == 201, cohort_added.text

    preview = await academy_client.get(
        f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/preview"
    )
    assert preview.status_code == 409, preview.text
    freeze = await academy_client.post(
        f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/freeze"
    )
    assert freeze.status_code == 409, freeze.text
    frozen = await academy_client.get(
        f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/frozen"
    )
    assert frozen.status_code == 404


@pytest.mark.asyncio
@pytest.mark.integration
async def test_legacy_milestones_are_not_rewritten_by_catalog_snapshots(
    academy_client, db_session
):
    from tests.factories import MilestoneFactory

    program = ProgramFactory.create()
    cohort = CohortFactory.create(program_id=program.id)
    legacy = MilestoneFactory.create(program_id=program.id)
    definition, version = _milestone(code="MS-TEST-NEW-PATH")
    db_session.add_all([program, cohort, legacy, definition, version])
    await db_session.commit()

    assign = await academy_client.post(
        f"{BASE}/programs/{program.id}/curriculum/milestones",
        json={"milestone_version_id": str(version.id)},
    )
    assert assign.status_code == 201, assign.text
    freeze = await academy_client.post(
        f"{BASE}/cohorts/{cohort.id}/curriculum/requirements/freeze"
    )
    assert freeze.status_code == 201, freeze.text

    legacy_result = await academy_client.get(
        f"/academy/programs/{program.id}/milestones"
    )
    assert legacy_result.status_code == 200, legacy_result.text
    assert len(legacy_result.json()) == 1
    assert legacy_result.json()[0]["id"] == str(legacy.id)
    assert legacy_result.json()[0]["name"] == legacy.name

    raw = await db_session.scalar(
        select(AcademyProgramMilestoneAssignment).where(
            AcademyProgramMilestoneAssignment.program_id == program.id
        )
    )
    assert raw.milestone_version_id == version.id
