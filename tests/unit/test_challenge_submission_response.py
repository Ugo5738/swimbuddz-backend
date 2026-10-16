"""Keep saved proof and timestamps in the member/admin submission contract."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from libs.auth.dependencies import get_current_user
from libs.db.session import get_async_db
from services.members_service.models import MemberChallengeCompletion
from services.members_service.routers.challenges import submissions
from services.members_service.routers.challenges._helpers import _responses
from services.members_service.schemas import (
    ChallengeSubmissionResponse,
    ChallengeSubmissionRevokeRequest,
)
from tests.conftest import make_member_user


@pytest.mark.asyncio
async def test_member_history_serializes_saved_proof_dates_and_roster(monkeypatch):
    now = datetime.now(timezone.utc)
    member_id, challenge_id, submission_id = (uuid.uuid4() for _ in range(3))
    media_id, roster_id = uuid.uuid4(), uuid.uuid4()
    submission = MemberChallengeCompletion(
        id=submission_id,
        member_id=member_id,
        challenge_id=challenge_id,
        submitted_by_member_id=member_id,
        is_team_submission=False,
        status="pending",
        completed_at=now,
        created_at=now,
        result_data='{"distance": 25}',
    )
    # Exercise the real hydration and FastAPI response serialization; only
    # database results and cross-service media/name lookups are substituted.
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(all=lambda: [(submission_id,)]),
                SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [submission])
                ),
                SimpleNamespace(
                    scalar_one_or_none=lambda: "Bottle Balance Back Sculling"
                ),
            ]
        )
    )
    monkeypatch.setattr(
        submissions, "_resolve_member_id_from_auth", AsyncMock(return_value=member_id)
    )
    monkeypatch.setattr(
        _responses,
        "_load_submission_proof_media",
        AsyncMock(
            return_value=[
                {
                    "id": uuid.uuid4(),
                    "media_id": media_id,
                    "order_idx": 0,
                    "file_url": "https://example.com/proof.mov",
                    "thumbnail_url": None,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        _responses,
        "_load_submission_members",
        AsyncMock(
            return_value=[
                {
                    "id": roster_id,
                    "member_id": member_id,
                    "badge_awarded": False,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        _responses,
        "_load_member_names",
        AsyncMock(return_value={member_id: "Test Member"}),
    )
    app = FastAPI()
    app.include_router(submissions.router, prefix="/challenges")
    app.dependency_overrides[get_async_db] = lambda: db
    app.dependency_overrides[get_current_user] = make_member_user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/challenges/submissions/mine?challenge_id={challenge_id}"
        )
    assert response.status_code == 200
    body = response.json()[0]
    assert (
        body["created_at"]
        == body["completed_at"]
        == now.isoformat().replace("+00:00", "Z")
    )
    assert body["proof_media"][0]["media_id"] == str(media_id)
    assert body["proof_media"][0]["file_url"].endswith("proof.mov")
    assert body["members"][0]["member_id"] == str(member_id)
    assert body["result_data"] == {"distance": 25}
    assert body["rewards_distributed_at"] is None


def test_legacy_submission_defaults_and_attribute_validation():
    now = datetime.now(timezone.utc)
    submission = SimpleNamespace(
        id=uuid.uuid4(),
        challenge_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        completed_at=now,
        created_at=now,
    )
    response = ChallengeSubmissionResponse.model_validate(submission).model_dump(
        mode="json"
    )
    assert response["proof_media"] == []
    assert response["members"] == []
    assert response["status"] == "pending"


def test_revoke_request_accepts_only_a_reason_and_still_validates_it():
    assert ChallengeSubmissionRevokeRequest(
        revoke_note="Wrong technique"
    ).model_dump() == {"revoke_note": "Wrong technique"}
    with pytest.raises(ValidationError):
        ChallengeSubmissionRevokeRequest(revoke_note="bad")


def test_openapi_assigns_submission_fields_to_response_only():
    from services.members_service.app.main import app

    schemas = app.openapi()["components"]["schemas"]
    response = schemas["ChallengeSubmissionResponse"]
    assert {
        "created_at",
        "completed_at",
        "proof_media",
        "members",
        "result_data",
        "rewards_distributed_at",
    } <= response["properties"].keys()
    assert set(schemas["ChallengeSubmissionRevokeRequest"]["properties"]) == {
        "revoke_note"
    }
