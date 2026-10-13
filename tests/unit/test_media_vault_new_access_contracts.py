"""Media Vault multi-vault access and member video safety contracts."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.media_service.schemas.vault import (
    BulkVaultGrantCreate,
    MemberVideoShareRequest,
    MultipartInitiateRequest,
)


@pytest.fixture
def window():
    start = datetime(2026, 10, 12, 14, tzinfo=timezone.utc)
    return {"starts_at": start, "expires_at": start + timedelta(days=30)}


def test_bulk_vault_grant_requires_selected_ids(window):
    with pytest.raises(ValidationError, match="Select at least one vault"):
        BulkVaultGrantCreate(
            member_id=uuid.uuid4(),
            role="curator",
            scope="selected",
            **window,
        )


def test_bulk_vault_grant_rejects_duplicate_ids(window):
    vault_id = uuid.uuid4()
    with pytest.raises(ValidationError, match="Duplicate vault IDs"):
        BulkVaultGrantCreate(
            member_id=uuid.uuid4(),
            role="curator",
            scope="selected",
            vault_ids=[vault_id, vault_id],
            **window,
        )


def test_bulk_grant_curator_can_download_but_not_administer(window):
    grant = BulkVaultGrantCreate(
        member_id=uuid.uuid4(),
        role="curator",
        scope="all_current",
        **window,
    )
    assert grant.can_download_originals is True
    with pytest.raises(ValidationError):
        BulkVaultGrantCreate(
            member_id=uuid.uuid4(),
            role="admin",
            scope="all_current",
            **window,
        )


def test_bulk_grant_all_current_does_not_accept_selected_ids(window):
    with pytest.raises(ValidationError, match="Do not supply vault_ids"):
        BulkVaultGrantCreate(
            member_id=uuid.uuid4(),
            role="contributor",
            scope="all_current",
            vault_ids=[uuid.uuid4()],
            **window,
        )


def test_upload_quick_tags_are_normalized_and_deduplicated():
    request = MultipartInitiateRequest(
        filename="practice.mov",
        content_type="video/quicktime",
        size_bytes=1024,
        labels=["  Academy ", "academy", "Progress"],
    )
    assert request.labels == ["Academy", "Progress"]


def test_upload_quick_tags_reject_invalid_length():
    with pytest.raises(ValidationError):
        MultipartInitiateRequest(
            filename="practice.mov",
            content_type="video/quicktime",
            size_bytes=1024,
            labels=["x" * 41],
        )


def test_member_video_share_is_explicit_and_bounded():
    request = MemberVideoShareRequest(
        media_item_ids=[uuid.uuid4()],
        member_ids=[uuid.uuid4()],
        action="revoke",
    )
    assert request.action == "revoke"
    with pytest.raises(ValidationError):
        MemberVideoShareRequest(media_item_ids=[], member_ids=[uuid.uuid4()])
