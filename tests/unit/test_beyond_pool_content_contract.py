"""Regression guards for public video episode metadata and attribution."""
import uuid

import pytest
from pydantic import ValidationError

from services.communications_service.schemas.main import (
    ContentPostCreate,
    ContentPostUpdate,
)


def _draft(**overrides):
    data = {
        "title": "Beyond the Pool",
        "summary": "Stories from swimmers",
        "body": "Episode highlights",
        "category": "beyond_the_pool",
        "tier_access": "community",
        "video_url": "https://www.youtube.com/live/g_4oasxw46M",
        "episode_number": 2,
        "guest_names": "Swimmers",
    }
    data.update(overrides)
    return ContentPostCreate(**data)


def test_episode_metadata_survives_content_create_and_update():
    draft = _draft()
    assert draft.video_url.endswith("g_4oasxw46M")
    assert draft.episode_number == 2
    assert draft.guest_names == "Swimmers"
    edited = ContentPostUpdate(video_url=draft.video_url, episode_number=3)
    assert edited.episode_number == 3


@pytest.mark.parametrize("url", [
    "https://evil.example.com/watch?v=g_4oasxw46M",
    "https://youtube.com.evil.example.com/watch?v=g_4oasxw46M",
    "http://www.youtube.com/watch?v=g_4oasxw46M",
    "https://www.youtube.com/watch?v=invalid",
])
def test_video_embed_url_rejects_untrusted_hosts_and_ids(url):
    with pytest.raises(ValidationError):
        _draft(video_url=url)


def test_episode_number_must_be_positive():
    with pytest.raises(ValidationError):
        _draft(episode_number=0)
