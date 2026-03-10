"""Audio tracks router: CRUD for audio overlay library + apply-audio endpoint."""

import uuid
from typing import List, Optional

from arq import create_pool
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.media_service.models import AudioTrack, LicenseType, MediaItem
from services.media_service.routers.media import _CHUNK_SIZE
from services.media_service.schemas import (
    ApplyAudioRequest,
    AudioTrackResponse,
    AudioTrackUpdate,
    MediaItemResponse,
)
from services.media_service.services.storage import BucketType, storage_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/media", tags=["audio"])

# ── Upload size limit for audio files ──
MAX_AUDIO_SIZE = 50 * 1024 * 1024  # 50 MB


async def _read_audio_with_limit(file: UploadFile) -> bytes:
    """Read uploaded audio file in chunks, enforcing 50MB limit."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Audio file too large. Maximum size is 50 MB.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# ── Lazy ARQ Redis pool ──
_redis_pool = None


async def _get_redis_pool():
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(get_redis_settings())
    return _redis_pool


# ============================================================================
# AUDIO TRACKS - CRUD
# ============================================================================


@router.get("/audio-tracks", response_model=List[AudioTrackResponse])
async def list_audio_tracks(
    genre: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_async_db),
):
    """List available audio tracks for audio overlay.

    Public endpoint — any authenticated user can browse the track library.
    """
    query = select(AudioTrack).where(AudioTrack.is_active.is_(True))

    if genre:
        query = query.where(AudioTrack.genre == genre)
    if search:
        search_term = f"%{search}%"
        query = query.where(
            AudioTrack.title.ilike(search_term) | AudioTrack.artist.ilike(search_term)
        )

    query = query.order_by(AudioTrack.title).limit(limit).offset(offset)
    result = await db.execute(query)
    tracks = result.scalars().all()

    return [AudioTrackResponse.model_validate(t) for t in tracks]


@router.get("/audio-tracks/{track_id}", response_model=AudioTrackResponse)
async def get_audio_track(
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single audio track by ID."""
    query = select(AudioTrack).where(AudioTrack.id == track_id)
    result = await db.execute(query)
    track = result.scalar_one_or_none()

    if not track:
        raise HTTPException(status_code=404, detail="Audio track not found")

    return AudioTrackResponse.model_validate(track)


@router.post(
    "/audio-tracks",
    response_model=AudioTrackResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_audio_track(
    file: UploadFile = File(...),
    title: str = Form(...),
    artist: Optional[str] = Form(None),
    genre: Optional[str] = Form(None),
    license_type: str = Form("ROYALTY_FREE"),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Upload a new audio track (admin only).

    Accepts MP3, WAV, AAC, OGG, and FLAC files. The file is stored
    in the public bucket under audio-tracks/ prefix.
    """
    content_type = file.content_type or ""
    allowed_audio_types = {
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/aac",
        "audio/ogg",
        "audio/flac",
        "audio/mp4",
        "audio/x-m4a",
    }
    if content_type not in allowed_audio_types and not content_type.startswith(
        "audio/"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be an audio file (MP3, WAV, AAC, OGG, or FLAC)",
        )

    # Validate license_type
    valid_types = {lt.value for lt in LicenseType}
    if license_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid license_type. Must be one of: {', '.join(sorted(valid_types))}",
        )

    file_data = await _read_audio_with_limit(file)

    # Upload to storage
    original_name = file.filename or f"audio_{uuid.uuid4()}"
    file_ext = original_name.rsplit(".", 1)[-1] if "." in original_name else "mp3"
    storage_name = f"audio-tracks/{uuid.uuid4()}.{file_ext}"

    file_url, _ = await storage_service.upload_media(
        file_data,
        storage_name,
        content_type or "audio/mpeg",
        bucket_type=BucketType.PUBLIC,
    )

    # Extract duration using ffprobe (best-effort)
    duration_seconds = await _probe_audio_duration(file_data)

    db_track = AudioTrack(
        title=title,
        artist=artist,
        file_url=file_url,
        duration_seconds=duration_seconds,
        genre=genre,
        license_type=LicenseType(license_type),
        uploaded_by=current_user.user_id,
    )
    db.add(db_track)
    await db.commit()
    await db.refresh(db_track)

    return AudioTrackResponse.model_validate(db_track)


@router.put("/audio-tracks/{track_id}", response_model=AudioTrackResponse)
async def update_audio_track(
    track_id: uuid.UUID,
    update: AudioTrackUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Update audio track metadata (admin only)."""
    query = select(AudioTrack).where(AudioTrack.id == track_id)
    result = await db.execute(query)
    track = result.scalar_one_or_none()

    if not track:
        raise HTTPException(status_code=404, detail="Audio track not found")

    update_data = update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(track, field, value)

    await db.commit()
    await db.refresh(track)

    return AudioTrackResponse.model_validate(track)


@router.delete("/audio-tracks/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audio_track(
    track_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Delete an audio track (admin only)."""
    query = select(AudioTrack).where(AudioTrack.id == track_id)
    result = await db.execute(query)
    track = result.scalar_one_or_none()

    if not track:
        raise HTTPException(status_code=404, detail="Audio track not found")

    # Delete from storage
    await storage_service.delete_media(track.file_url)

    await db.delete(track)
    await db.commit()


# ============================================================================
# APPLY AUDIO TO VIDEO
# ============================================================================


@router.post(
    "/videos/{media_id}/apply-audio",
    response_model=MediaItemResponse,
)
async def apply_audio_to_video(
    media_id: uuid.UUID,
    body: ApplyAudioRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply an audio track to a video (admin only).

    Enqueues an async job that uses ffmpeg to:
    - Strip or mix the original audio
    - Overlay the selected audio track
    - Re-upload the processed video

    volume_mix controls the blend:
    - 1.0 = fully replace original audio with the track
    - 0.0 = keep original audio only (no-op)
    - 0.5 = mix both at equal volume
    """
    # Verify the media item exists and is a video
    query = select(MediaItem).where(MediaItem.id == media_id)
    result = await db.execute(query)
    media_item = result.scalar_one_or_none()

    if not media_item:
        raise HTTPException(status_code=404, detail="Media item not found")

    media_type_val = (
        media_item.media_type.value
        if hasattr(media_item.media_type, "value")
        else media_item.media_type
    )
    if media_type_val != "VIDEO":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Media item must be a video",
        )

    # Verify the audio track exists and is active
    audio_query = select(AudioTrack).where(
        AudioTrack.id == body.audio_track_id,
        AudioTrack.is_active.is_(True),
    )
    audio_result = await db.execute(audio_query)
    audio_track = audio_result.scalar_one_or_none()

    if not audio_track:
        raise HTTPException(status_code=404, detail="Audio track not found or inactive")

    # Mark video as unprocessed while audio is being applied
    media_item.is_processed = False
    await db.commit()
    await db.refresh(media_item)

    # Enqueue the audio overlay job
    try:
        pool = await _get_redis_pool()
        await pool.enqueue_job(
            "task_apply_audio",
            str(media_item.id),
            media_item.file_url,
            audio_track.file_url,
            body.volume_mix,
            body.start_offset_seconds,
            _queue_name="arq:media",
        )
        logger.info(
            "Enqueued audio overlay for media %s with track %s",
            media_id,
            body.audio_track_id,
        )
    except Exception as e:
        logger.warning("Failed to enqueue audio overlay: %s", e)
        # Restore processed state since job wasn't queued
        media_item.is_processed = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audio processing queue is temporarily unavailable. Please try again later.",
        )

    # Return the media item (is_processed=False indicates processing)
    from services.media_service.routers._helpers import _build_media_item_response

    return await _build_media_item_response(db, media_item)


# ── Helpers ──


async def _probe_audio_duration(file_data: bytes) -> Optional[float]:
    """Extract audio duration using ffprobe. Best-effort, returns None on failure."""
    import json
    import os
    import subprocess
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        os.unlink(tmp_path)

        if result.returncode == 0:
            data = json.loads(result.stdout)
            duration = data.get("format", {}).get("duration")
            if duration:
                return round(float(duration), 2)
    except Exception as e:
        logger.debug("ffprobe audio duration failed: %s", e)

    return None
