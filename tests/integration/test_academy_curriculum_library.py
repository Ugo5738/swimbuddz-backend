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
        ("milestone", {"rubric_json": {"distance_m": 25}, "evidence_policy": "video"}),
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
