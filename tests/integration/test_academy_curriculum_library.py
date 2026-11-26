"""Contract tests for reusable Academy catalog authoring and version publication."""

import uuid

import pytest
from sqlalchemy import select

from services.academy_service.models import (
    AcademyMilestoneDefinition,
    AcademyMilestoneVersion,
)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "content"),
    [
        (
            "milestone",
            {
                "rubric_json": {
                    "distance_m": 25,
                    "criteria": ["continuous-25m-unassisted"],
                },
                "evidence_policy": "video",
            },
        ),
        (
            "drill",
            {
                "steps_json": ["Hold the wall and exhale"],
                "primary_focal_cue": "Slow exhale",
            },
        ),
        (
            "lesson",
            {
                "duration_minutes": 60,
                "blocks_json": [{"minutes": 10, "name": "Warmup"}],
            },
        ),
        ("quiz", {"questions_json": [{"id": "q1", "prompt": "Pool rules?"}]}),
        ("module", {"outcomes_json": ["Safe water entry"]}),
    ],
)
async def test_admin_can_create_version_and_publish(academy_client, kind, content):
    code = "LIB_" + uuid.uuid4().hex[:14].upper()
    created = await academy_client.post(
        f"/academy/admin/curriculum-library/{kind}",
        json={"code": code, "title": "Reusable Academy Content"},
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]
    draft = await academy_client.post(
        f"/academy/admin/curriculum-library/{kind}/{definition_id}/versions",
        json={"content": content},
    )
    assert draft.status_code == 201, draft.text
    payload = draft.json()
    assert payload["state"] == "draft"
    assert payload["version"] == 1

    published = await academy_client.post(
        f"/academy/admin/curriculum-library/{kind}/{definition_id}/versions/{payload['id']}/publish"
    )
    assert published.status_code == 200, published.text
    assert published.json()["state"] == "published"
    assert published.json()["published_at"] is not None

    replay = await academy_client.post(
        f"/academy/admin/curriculum-library/{kind}/{definition_id}/versions/{payload['id']}/publish"
    )
    assert replay.status_code == 200
    assert replay.json()["published_at"] == published.json()["published_at"]

    second = await academy_client.post(
        f"/academy/admin/curriculum-library/{kind}/{definition_id}/versions",
        json={"content": content},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2

    listed = await academy_client.get(
        f"/academy/admin/curriculum-library/{kind}/{definition_id}/versions"
    )
    assert listed.status_code == 200
    assert [x["version"] for x in listed.json()] == [2, 1]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_milestone_requires_usable_rubric_and_valid_evidence_policy(
    academy_client, db_session
):
    code = "LIB_" + uuid.uuid4().hex[:14].upper()
    created = await academy_client.post(
        "/academy/admin/curriculum-library/milestone",
        json={"code": code, "title": "Independent water safety outcome"},
    )
    assert created.status_code == 201, created.text
    definition_id = created.json()["id"]

    for content in [
        {"rubric_json": {}},
        {"rubric_json": {"time_s": 30}, "evidence_policy": "no_verification"},
        {"rubric_json": {"time_s": 30}, "assessment_context_json": []},
    ]:
        response = await academy_client.post(
            f"/academy/admin/curriculum-library/milestone/{definition_id}/versions",
            json={"content": content},
        )
        assert response.status_code == 422, response.text

    record = await db_session.get(AcademyMilestoneDefinition, uuid.UUID(definition_id))
    assert record.code == code
    versions = (
        (
            await db_session.execute(
                select(AcademyMilestoneVersion).where(
                    AcademyMilestoneVersion.definition_id == record.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert versions == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_duplicate_catalog_code_is_rejected(academy_client):
    code = "LIB_" + uuid.uuid4().hex[:14].upper()
    payload = {"code": code, "title": "Reusable content"}
    first = await academy_client.post(
        "/academy/admin/curriculum-library/drill", json=payload
    )
    assert first.status_code == 201, first.text
    second = await academy_client.post(
        "/academy/admin/curriculum-library/drill", json=payload
    )
    assert second.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_publish_time_milestone_contract_draft_edit_and_retire(academy_client):
    code = "MS_PUB_" + uuid.uuid4().hex[:12].upper()
    created = await academy_client.post(
        "/academy/admin/curriculum-library/milestone",
        json={"code": code, "title": "Publish-time contract milestone"},
    )
    assert created.status_code == 201, created.text
    def_id = created.json()["id"]

    # Draft with empty criteria can be saved, but cannot be published.
    draft = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions",
        json={
            "content": {
                "rubric_json": {"goal": "Swim calmly", "criteria": []},
                "evidence_policy": "video",
                "safety_gate": True,
                "assessment_context_json": {},
            }
        },
    )
    assert draft.status_code == 201, draft.text
    ver_id = draft.json()["id"]

    blocked_criteria = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}/publish"
    )
    assert blocked_criteria.status_code == 422, blocked_criteria.text

    # Fix criteria via PATCH while still draft, but omit validity_days on safety gate.
    patched_no_validity = await academy_client.patch(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}",
        json={
            "content": {
                "rubric_json": {
                    "goal": "Swim calmly",
                    "criteria": ["calm-entry", "controlled-exit"],
                },
                "evidence_policy": "video",
                "safety_gate": True,
                "assessment_context_json": {},
            }
        },
    )
    assert patched_no_validity.status_code == 200, patched_no_validity.text

    blocked_validity = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}/publish"
    )
    assert blocked_validity.status_code == 422, blocked_validity.text

    # Add validity_days via PATCH and publish cleanly.
    patched_ready = await academy_client.patch(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}",
        json={
            "content": {
                "rubric_json": {
                    "goal": "Swim calmly",
                    "criteria": ["calm-entry", "controlled-exit"],
                },
                "evidence_policy": "video",
                "safety_gate": True,
                "assessment_context_json": {"validity_days": 365},
            }
        },
    )
    assert patched_ready.status_code == 200, patched_ready.text

    published = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}/publish"
    )
    assert published.status_code == 200, published.text

    # Published version is immutable.
    edit_published = await academy_client.patch(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions/{ver_id}",
        json={
            "content": {
                "rubric_json": {"criteria": ["altered"]},
                "evidence_policy": "video",
                "safety_gate": True,
                "assessment_context_json": {"validity_days": 365},
            }
        },
    )
    assert edit_published.status_code == 409

    # Retiring blocks new versions.
    retired = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/retire"
    )
    assert retired.status_code == 200
    assert retired.json()["is_retired"] is True

    after_retire = await academy_client.post(
        f"/academy/admin/curriculum-library/milestone/{def_id}/versions",
        json={
            "content": {
                "rubric_json": {"criteria": ["x"]},
                "evidence_policy": "video",
            }
        },
    )
    assert after_retire.status_code == 409


@pytest.mark.asyncio
@pytest.mark.integration
async def test_catalog_composition_and_skill_links(academy_client, db_session):
    from services.academy_service.models import Skill

    skill = Skill(name="Rhythmic Exhalation", category="breathing")
    db_session.add(skill)
    await db_session.commit()

    skills_res = await academy_client.get("/academy/admin/curriculum-library/skills")
    assert skills_res.status_code == 200
    assert any(s["id"] == str(skill.id) for s in skills_res.json())

    # Create + publish a drill, link a skill to it.
    drill_def = (
        await academy_client.post(
            "/academy/admin/curriculum-library/drill",
            json={
                "code": "DRL_" + uuid.uuid4().hex[:10].upper(),
                "title": "Humming Bobs",
            },
        )
    ).json()
    drill_v1 = (
        await academy_client.post(
            f"/academy/admin/curriculum-library/drill/{drill_def['id']}/versions",
            json={
                "content": {
                    "steps_json": ["Hum underwater steadily"],
                    "primary_focal_cue": "Continuous gentle hum",
                }
            },
        )
    ).json()
    await academy_client.post(
        f"/academy/admin/curriculum-library/drill/{drill_def['id']}/versions/{drill_v1['id']}/publish"
    )
    drill_skill = await academy_client.post(
        f"/academy/admin/curriculum-library/drills/{drill_def['id']}/skills",
        json={"skill_id": str(skill.id)},
    )
    assert drill_skill.status_code == 201, drill_skill.text
    listed_drill_skills = await academy_client.get(
        f"/academy/admin/curriculum-library/drills/{drill_def['id']}/skills"
    )
    assert len(listed_drill_skills.json()) == 1

    # Create draft lesson, attach published drill, list, then publish & verify write lock.
    lesson_def = (
        await academy_client.post(
            "/academy/admin/curriculum-library/lesson",
            json={
                "code": "LSN_" + uuid.uuid4().hex[:10].upper(),
                "title": "Breath Control",
            },
        )
    ).json()
    lesson_v1 = (
        await academy_client.post(
            f"/academy/admin/curriculum-library/lesson/{lesson_def['id']}/versions",
            json={
                "content": {
                    "duration_minutes": 60,
                    "blocks_json": [{"duration_minutes": 20, "activity": "Bobs"}],
                }
            },
        )
    ).json()
    attached_drill = await academy_client.post(
        f"/academy/admin/curriculum-library/lessons/{lesson_v1['id']}/drills",
        json={"drill_version_id": drill_v1["id"], "order_index": 0, "sets": 3},
    )
    assert attached_drill.status_code == 201, attached_drill.text
    lesson_drills = await academy_client.get(
        f"/academy/admin/curriculum-library/lessons/{lesson_v1['id']}/drills"
    )
    assert lesson_drills.status_code == 200
    assert lesson_drills.json()[0]["drill_code"] == drill_def["code"]

    await academy_client.post(
        f"/academy/admin/curriculum-library/lesson/{lesson_def['id']}/versions/{lesson_v1['id']}/publish"
    )
    # Removing drill after lesson publish is blocked.
    remove_after_pub = await academy_client.delete(
        f"/academy/admin/curriculum-library/lessons/{lesson_v1['id']}/drills/{attached_drill.json()['id']}"
    )
    assert remove_after_pub.status_code == 409

    # Create + publish a quiz, create draft module, attach lesson + quiz, inspect composition.
    quiz_def = (
        await academy_client.post(
            "/academy/admin/curriculum-library/quiz",
            json={
                "code": "QZ_" + uuid.uuid4().hex[:10].upper(),
                "title": "Breathing Quiz",
            },
        )
    ).json()
    quiz_v1 = (
        await academy_client.post(
            f"/academy/admin/curriculum-library/quiz/{quiz_def['id']}/versions",
            json={
                "content": {"questions_json": [{"id": "q1", "prompt": "Why exhale?"}]}
            },
        )
    ).json()
    await academy_client.post(
        f"/academy/admin/curriculum-library/quiz/{quiz_def['id']}/versions/{quiz_v1['id']}/publish"
    )
    mod_def = (
        await academy_client.post(
            "/academy/admin/curriculum-library/module",
            json={"code": "MOD_" + uuid.uuid4().hex[:10].upper(), "title": "F2 Module"},
        )
    ).json()
    mod_v1 = (
        await academy_client.post(
            f"/academy/admin/curriculum-library/module/{mod_def['id']}/versions",
            json={"content": {"outcomes_json": ["20 rhythmic bobs"]}},
        )
    ).json()
    ml_res = await academy_client.post(
        f"/academy/admin/curriculum-library/modules/{mod_v1['id']}/lessons",
        json={"lesson_version_id": lesson_v1["id"], "order_index": 0},
    )
    assert ml_res.status_code == 201, ml_res.text
    mq_res = await academy_client.post(
        f"/academy/admin/curriculum-library/modules/{mod_v1['id']}/quizzes",
        json={"quiz_version_id": quiz_v1["id"], "required_for_completion": True},
    )
    assert mq_res.status_code == 201, mq_res.text

    comp = await academy_client.get(
        f"/academy/admin/curriculum-library/modules/{mod_v1['id']}/composition"
    )
    assert comp.status_code == 200
    assert len(comp.json()["lessons"]) == 1
    assert len(comp.json()["quizzes"]) == 1

    # Remove draft quiz before publishing succeeds.
    del_q = await academy_client.delete(
        f"/academy/admin/curriculum-library/modules/{mod_v1['id']}/quizzes/{mq_res.json()['id']}"
    )
    assert del_q.status_code == 204


@pytest.mark.asyncio
@pytest.mark.integration
async def test_baseline_v2_seed_is_idempotent(db_session):
    from scripts.seed.academy_curriculum_v2 import seed_academy_curriculum_v2

    first = await seed_academy_curriculum_v2(db_session)
    assert first["modules_created"] >= 6
    assert first["milestones_created"] >= 7
    assert first["drills_created"] >= 8

    second = await seed_academy_curriculum_v2(db_session)
    assert all(count == 0 for count in second.values())
