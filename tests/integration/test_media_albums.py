"""Integration tests for media_service — album CRUD + media read guards.

The album endpoints are the service's pure-DB business logic: create
stamps the actor, list derives media_count + a cover, get returns the
ordered media set, and delete removes the album *without* destroying the
underlying MediaItems (they may live in other albums). These are the
guards worth pinning; the upload/presign paths touch object storage and
belong with a storage-mocked suite.

All album writes are admin-gated; `_wire_app` already overrides
`require_admin` to an admin user, so `media_client` exercises them
directly. `list_*` endpoints return *all* rows (shared test DB may carry
seed data) so assertions look up the created row by id rather than
asserting list length.

Not in scope (follow-up): /uploads + /register-url (object storage),
presigned-URL rewriting, audio tracks, media tagging, site-asset CRUD.
"""

import uuid

import pytest


def _album_payload(**overrides):
    s = uuid.uuid4().hex[:8]
    d = {
        "title": f"Gallery {s}",
        "description": "test album",
        "album_type": "general",
        "is_public": True,
        "slug": f"gallery-{s}",
    }
    d.update(overrides)
    return d


def _make_media_item(**overrides):
    from services.media_service.models import MediaItem

    s = uuid.uuid4().hex[:8]
    d = {
        "id": uuid.uuid4(),
        "media_type": "image",
        "file_url": f"https://cdn.example.com/{s}.jpg",
        "thumbnail_url": f"https://cdn.example.com/{s}-thumb.jpg",
        "title": f"Photo {s}",
        "uploaded_by": uuid.uuid4(),
        "is_processed": True,
    }
    d.update(overrides)
    return MediaItem(**d)


# ---------------------------------------------------------------------------
# create / list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_album_stamps_actor_and_zero_count(media_client):
    resp = await media_client.post("/media/albums", json=_album_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_count"] == 0
    assert body["created_by"]  # actor stamped from the JWT
    assert body["id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_albums_includes_created_and_filters_by_type(media_client):
    marker_type = f"itest_{uuid.uuid4().hex[:8]}"
    created = await media_client.post(
        "/media/albums", json=_album_payload(album_type=marker_type)
    )
    assert created.status_code == 200, created.text
    album_id = created.json()["id"]

    # Filtered by our unique type → exactly the one we just made.
    filtered = await media_client.get(f"/media/albums?album_type={marker_type}")
    assert filtered.status_code == 200, filtered.text
    rows = filtered.json()
    assert [r["id"] for r in rows] == [album_id]
    assert rows[0]["media_count"] == 0


# ---------------------------------------------------------------------------
# get (404 + with media)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_unknown_album_404(media_client):
    resp = await media_client.get(f"/media/albums/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_album_returns_ordered_media(media_client, db_session):
    from services.media_service.models import AlbumItem

    created = await media_client.post("/media/albums", json=_album_payload())
    album_id = uuid.UUID(created.json()["id"])

    m1 = _make_media_item(title="first")
    m2 = _make_media_item(title="second")
    db_session.add_all([m1, m2])
    await db_session.flush()
    db_session.add_all(
        [
            AlbumItem(album_id=album_id, media_item_id=m1.id, order=1),
            AlbumItem(album_id=album_id, media_item_id=m2.id, order=0),
        ]
    )
    await db_session.commit()

    resp = await media_client.get(f"/media/albums/{album_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["media_count"] == 2
    # AlbumItem.order ASC → m2 (order 0) before m1 (order 1).
    assert [item["title"] for item in body["media_items"]] == ["second", "first"]


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_update_unknown_album_404(media_client):
    resp = await media_client.patch(
        f"/media/albums/{uuid.uuid4()}", json={"title": "x"}
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_patch_album_changes_title(media_client):
    created = await media_client.post("/media/albums", json=_album_payload())
    album_id = created.json()["id"]

    resp = await media_client.patch(
        f"/media/albums/{album_id}", json={"title": "Renamed"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["title"] == "Renamed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_album_keeps_media_then_404(media_client, db_session):
    """Deleting an album removes it (and AlbumItems) but the MediaItem
    survives — it may belong to other albums or stand alone."""
    from sqlalchemy import select

    from services.media_service.models import AlbumItem, MediaItem

    created = await media_client.post("/media/albums", json=_album_payload())
    album_id = uuid.UUID(created.json()["id"])

    media = _make_media_item()
    db_session.add(media)
    await db_session.flush()
    media_id = media.id
    db_session.add(AlbumItem(album_id=album_id, media_item_id=media_id, order=0))
    await db_session.commit()

    deleted = await media_client.delete(f"/media/albums/{album_id}")
    assert deleted.status_code == 200, deleted.text

    gone = await media_client.get(f"/media/albums/{album_id}")
    assert gone.status_code == 404, gone.text

    # MediaItem must NOT be cascade-deleted with the album.
    still_there = (
        await db_session.execute(
            select(MediaItem).where(MediaItem.id == media_id)
        )
    ).scalar_one_or_none()
    assert still_there is not None
    # ...but its AlbumItem link is gone.
    orphan_link = (
        await db_session.execute(
            select(AlbumItem).where(AlbumItem.album_id == album_id)
        )
    ).scalar_one_or_none()
    assert orphan_link is None


# ---------------------------------------------------------------------------
# media item read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_unknown_media_item_404(media_client):
    resp = await media_client.get(f"/media/media/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_media_item_roundtrip(media_client, db_session):
    media = _make_media_item(title="roundtrip")
    db_session.add(media)
    await db_session.commit()
    media_id = media.id

    resp = await media_client.get(f"/media/media/{media_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(media_id)
    assert body["title"] == "roundtrip"
