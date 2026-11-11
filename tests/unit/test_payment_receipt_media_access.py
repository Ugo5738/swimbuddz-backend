import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.media_service.routers.media import (
    _media_playback_token,
    _require_original_access,
    _valid_media_playback_token,
)
from tests.conftest import make_admin_user, make_coach_user, make_member_user


@pytest.mark.parametrize(
    "metadata,url",
    [
        ({"purpose": "payment_proof"}, "https://storage/receipt"),
        ({}, "https://storage/payment-proofs/PAY-123/receipt.png"),
    ],
)
def test_only_receipt_uploader_and_admin_can_resolve_or_play_receipt(metadata, url):
    receipt = SimpleNamespace(metadata_info=metadata, file_url=url, uploaded_by="owner")
    for user in (
        None,
        make_member_user(user_id="someone-else"),
        make_coach_user(user_id="coach-1"),
    ):
        with pytest.raises(HTTPException) as exc:
            _require_original_access(receipt, user)
        assert exc.value.status_code == 404
    _require_original_access(receipt, make_member_user(user_id="owner"))
    _require_original_access(receipt, make_admin_user())


def test_coach_can_access_student_milestone_evidence_and_club_assessment():
    evidence = SimpleNamespace(
        metadata_info={"purpose": "milestone_evidence"},
        file_url="https://storage/private/evidence.mp4",
        uploaded_by="student-1",
    )
    # Student who uploaded can access
    _require_original_access(evidence, make_member_user(user_id="student-1"))
    # Coach can access to review
    _require_original_access(evidence, make_coach_user(user_id="coach-1"))
    # Admin can access
    _require_original_access(evidence, make_admin_user(user_id="admin-1"))

    # Unrelated member cannot access
    with pytest.raises(HTTPException) as exc:
        _require_original_access(evidence, make_member_user(user_id="stranger"))
    assert exc.value.status_code == 404

    # Both API inspection and browser playback deny anonymous UUID guessing.
    for playback in (False, True):
        with pytest.raises(HTTPException) as exc:
            _require_original_access(evidence, None, is_playback_route=playback)
        assert exc.value.status_code == 404

    # Club assessment video has matching coach/admin review access
    club_video = SimpleNamespace(
        metadata_info={"purpose": "club_assessment_video"},
        file_url="https://storage/private/club_assessment.mp4",
        uploaded_by="applicant-1",
    )
    _require_original_access(club_video, make_member_user(user_id="applicant-1"))
    _require_original_access(club_video, make_coach_user(user_id="coach-1"))
    _require_original_access(club_video, make_admin_user(user_id="admin-1"))
    with pytest.raises(HTTPException) as exc:
        _require_original_access(club_video, make_member_user(user_id="stranger"))
    assert exc.value.status_code == 404


def test_private_playback_tickets_are_media_bound_expiring_capabilities():
    evidence_id = uuid.uuid4()
    other_id = uuid.uuid4()
    expires = int(time.time()) + 300
    token = _media_playback_token(evidence_id, expires)
    assert _valid_media_playback_token(evidence_id, expires, token)
    assert not _valid_media_playback_token(other_id, expires, token)
    assert not _valid_media_playback_token(evidence_id, expires, "wrong")
    assert not _valid_media_playback_token(evidence_id, expires - 1000, token)
    assert not _valid_media_playback_token(evidence_id, expires + 10000, token)
    assert not _valid_media_playback_token(evidence_id, None, None)


def test_milestone_video_accessible_to_authenticated_users_and_playback():
    demo = SimpleNamespace(
        metadata_info={"purpose": "milestone_video"},
        file_url="https://storage/private/demo.mp4",
        uploaded_by="admin-1",
    )
    _require_original_access(demo, make_member_user(user_id="learner-1"))
    _require_original_access(demo, make_coach_user(user_id="coach-1"))
    _require_original_access(demo, make_admin_user(user_id="admin-1"))
    _require_original_access(demo, None, is_playback_route=True)
    with pytest.raises(HTTPException) as exc:
        _require_original_access(demo, None, is_playback_route=False)
    assert exc.value.status_code == 404


def test_public_images_remain_public():
    _require_original_access(
        SimpleNamespace(metadata_info={}, file_url="https://storage/public/cap.png"),
        None,
    )


async def test_anonymous_batch_and_list_queries_exclude_receipt_metadata_and_legacy_paths():
    import uuid
    from unittest.mock import AsyncMock
    from sqlalchemy.dialects import postgresql
    from services.media_service.routers.assets import resolve_media_urls
    from services.media_service.routers.media import list_media

    result = SimpleNamespace(
        fetchall=lambda: [], scalars=lambda: SimpleNamespace(all=lambda: [])
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert await resolve_media_urls([str(uuid.uuid4())], db) == {}
    assert await list_media(db=db) == []
    for call in db.execute.await_args_list:
        sql = str(
            call.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "payment_proof" in sql
        assert "payment-proofs/" in sql
        assert "NOT LIKE" in sql
