"""Video processing: transcode, generate thumbnails, extract metadata.

Uses ffmpeg/ffprobe via subprocess for reliability and transparency.
Designed to run as an ARQ background task outside the FastAPI request cycle.
"""

import json
import os
import subprocess
import tempfile
import uuid

import httpx
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from sqlalchemy import select

from services.media_service.models import MediaItem
from services.media_service.services.storage import BucketType, storage_service

logger = get_logger(__name__)

# ── ffprobe helpers ──


def _probe_video(filepath: str) -> dict:
    """Extract video metadata using ffprobe. Returns dict with duration, width, height, codec, etc."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            filepath,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("ffprobe failed: %s", result.stderr)
            return {}
        data = json.loads(result.stdout)

        # Find the video stream
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
            {},
        )
        fmt = data.get("format", {})

        return {
            "duration_seconds": float(fmt.get("duration", 0)),
            "file_size_bytes": int(fmt.get("size", 0)),
            "video_codec": video_stream.get("codec_name", "unknown"),
            "audio_codec": audio_stream.get("codec_name", "none"),
            "width": int(video_stream.get("width", 0)),
            "height": int(video_stream.get("height", 0)),
            "bitrate_kbps": int(fmt.get("bit_rate", 0)) // 1000
            if fmt.get("bit_rate")
            else 0,
            "fps": _parse_fps(video_stream.get("r_frame_rate", "0/1")),
        }
    except Exception as e:
        logger.error("ffprobe error: %s", e)
        return {}


def _parse_fps(r_frame_rate: str) -> float:
    """Parse ffprobe r_frame_rate like '30000/1001' to float."""
    try:
        num, den = r_frame_rate.split("/")
        return round(int(num) / int(den), 2) if int(den) else 0
    except (ValueError, ZeroDivisionError):
        return 0


# ── ffmpeg operations ──


def _transcode_video(input_path: str, output_path: str) -> bool:
    """Transcode video to web-optimized H.264/AAC MP4.

    - CRF 23 (good quality / reasonable size)
    - Preset medium (balanced speed / compression)
    - Max resolution 1280x720 (scale down if larger, preserve aspect ratio)
    - faststart for progressive web playback
    """
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            # Video
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "medium",
            "-vf",
            "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-pix_fmt",
            "yuv420p",
            # Audio
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            # Container
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            output_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout
        )
        if result.returncode != 0:
            logger.error("ffmpeg transcode failed: %s", result.stderr[-500:])
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg transcode timed out (10 min)")
        return False
    except Exception as e:
        logger.error("ffmpeg transcode error: %s", e)
        return False


def _extract_thumbnail(
    input_path: str, output_path: str, time_offset: float = 1.0
) -> bool:
    """Extract a poster frame from the video at the given time offset.

    Outputs a 600px-wide JPEG thumbnail.
    """
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(time_offset),
            "-i",
            input_path,
            "-vframes",
            "1",
            "-vf",
            "scale=600:-2",
            "-q:v",
            "2",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            # If time_offset is past the video end, try 0
            if time_offset > 0:
                return _extract_thumbnail(input_path, output_path, time_offset=0)
            logger.warning(
                "ffmpeg thumbnail extraction failed: %s", result.stderr[-300:]
            )
            return False
        return True
    except Exception as e:
        logger.error("ffmpeg thumbnail error: %s", e)
        return False


# ── Main processing function ──


async def process_video_upload(
    media_item_id: str,
    original_file_url: str,
    bucket_type_value: str,
) -> dict:
    """Process a video upload: download, probe, transcode, thumbnail, re-upload.

    Args:
        media_item_id: UUID string of the MediaItem record
        original_file_url: URL where the original video is stored
        bucket_type_value: "public" or "private"

    Returns:
        dict with processing results
    """
    logger.info("Processing video %s", media_item_id)
    bucket_type = BucketType(bucket_type_value)

    with tempfile.TemporaryDirectory(prefix="swimbuddz_video_") as tmpdir:
        original_path = os.path.join(tmpdir, "original")
        transcoded_path = os.path.join(tmpdir, f"transcoded_{uuid.uuid4()}.mp4")
        thumbnail_path = os.path.join(tmpdir, f"thumb_{uuid.uuid4()}.jpg")

        # Step 1: Download original video
        logger.info("Downloading video from %s", original_file_url[:80])
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                async with client.stream("GET", original_file_url) as resp:
                    resp.raise_for_status()
                    with open(original_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
        except Exception as e:
            logger.error("Failed to download video: %s", e)
            await _mark_processed_with_error(media_item_id, f"download_failed: {e}")
            return {"error": "download_failed"}

        original_size = os.path.getsize(original_path)
        logger.info("Downloaded %d bytes", original_size)

        # Step 2: Probe metadata
        metadata = _probe_video(original_path)
        metadata["original_size_bytes"] = original_size
        logger.info(
            "Video: %dx%d, %.1fs, %s",
            metadata.get("width", 0),
            metadata.get("height", 0),
            metadata.get("duration_seconds", 0),
            metadata.get("video_codec", "unknown"),
        )

        # Step 3: Transcode
        transcode_success = _transcode_video(original_path, transcoded_path)

        if transcode_success and os.path.exists(transcoded_path):
            transcoded_size = os.path.getsize(transcoded_path)
            metadata["transcoded_size_bytes"] = transcoded_size
            metadata["compression_ratio"] = (
                round(transcoded_size / original_size, 2) if original_size > 0 else 1.0
            )
            logger.info(
                "Transcoded: %d bytes (%.0f%% of original)",
                transcoded_size,
                (transcoded_size / original_size * 100) if original_size else 100,
            )
        else:
            logger.warning("Transcode failed, keeping original")
            metadata["transcode_error"] = "ffmpeg_failed"
            transcoded_path = None

        # Step 4: Extract thumbnail
        source_for_thumb = transcoded_path or original_path
        thumb_success = _extract_thumbnail(source_for_thumb, thumbnail_path)
        if not thumb_success:
            thumbnail_path = None
            logger.warning("Thumbnail extraction failed")

        # Step 5: Upload transcoded file (if available)
        new_file_url = None
        new_thumbnail_url = None

        if transcoded_path and os.path.exists(transcoded_path):
            with open(transcoded_path, "rb") as f:
                transcoded_data = f.read()

            storage_name = f"product-videos/{uuid.uuid4()}.mp4"
            new_file_url, _ = await storage_service.upload_media(
                transcoded_data,
                storage_name,
                "video/mp4",
                bucket_type=bucket_type,
            )
            logger.info("Uploaded transcoded video: %s", new_file_url[:80])

        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, "rb") as f:
                thumb_data = f.read()

            thumb_name = f"product-videos/{uuid.uuid4()}_thumb.jpg"
            new_thumbnail_url, _ = await storage_service.upload_media(
                thumb_data,
                thumb_name,
                "image/jpeg",
                bucket_type=BucketType.PUBLIC,
            )
            logger.info("Uploaded thumbnail: %s", new_thumbnail_url[:80])

        # Step 6: Update database record
        await _update_media_item(
            media_item_id=media_item_id,
            new_file_url=new_file_url,
            new_thumbnail_url=new_thumbnail_url,
            metadata=metadata,
        )

        logger.info("Video processing complete for %s", media_item_id)
        return {"status": "ok", "metadata": metadata}


# ── Database helpers ──


async def _update_media_item(
    media_item_id: str,
    new_file_url: str | None,
    new_thumbnail_url: str | None,
    metadata: dict,
) -> None:
    """Update the MediaItem with transcoded URLs and metadata."""
    async with AsyncSessionLocal() as db:
        query = select(MediaItem).where(MediaItem.id == media_item_id)
        result = await db.execute(query)
        item = result.scalar_one_or_none()

        if not item:
            logger.error("MediaItem %s not found", media_item_id)
            return

        if new_file_url:
            item.file_url = new_file_url
        if new_thumbnail_url:
            item.thumbnail_url = new_thumbnail_url

        item.metadata_info = metadata
        item.is_processed = True

        await db.commit()
        logger.info("Updated MediaItem %s: is_processed=True", media_item_id)


async def _mark_processed_with_error(media_item_id: str, error: str) -> None:
    """Mark a MediaItem as processed but with an error, so the original remains accessible."""
    async with AsyncSessionLocal() as db:
        query = select(MediaItem).where(MediaItem.id == media_item_id)
        result = await db.execute(query)
        item = result.scalar_one_or_none()

        if not item:
            return

        item.is_processed = True
        item.metadata_info = {"transcode_error": error}
        await db.commit()
        logger.warning(
            "Marked MediaItem %s processed with error: %s", media_item_id, error
        )


# ── Audio overlay operations ──


def _apply_audio_to_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    volume_mix: float = 1.0,
    audio_start_offset: float = 0.0,
) -> bool:
    """Apply audio track to video using ffmpeg.

    volume_mix controls the blend between original and new audio:
    - 1.0 = fully replace original audio with the track
    - 0.0 = keep original audio only (effectively a no-op)
    - 0.5 = mix both at equal volume

    audio_start_offset: seconds into the audio track to start from
    """
    try:
        if volume_mix >= 0.99:
            # Full replacement: strip original audio, use track audio
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ss",
                str(audio_start_offset),
                "-i",
                audio_path,
                # Map video from first input, audio from second input
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                # Keep video codec, re-encode audio to AAC
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                # Truncate audio to video length
                "-shortest",
                "-movflags",
                "+faststart",
                output_path,
            ]
        elif volume_mix <= 0.01:
            # No audio change — just copy
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-c",
                "copy",
                output_path,
            ]
        else:
            # Mix: blend original audio with track audio
            # amix: original at (1 - volume_mix), track at volume_mix
            orig_vol = round(1.0 - volume_mix, 2)
            track_vol = round(volume_mix, 2)
            filter_complex = (
                f"[0:a]volume={orig_vol}[orig];"
                f"[1:a]volume={track_vol}[track];"
                f"[orig][track]amix=inputs=2:duration=first:dropout_transition=2[mixed]"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-ss",
                str(audio_start_offset),
                "-i",
                audio_path,
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[mixed]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                output_path,
            ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        if result.returncode != 0:
            logger.error("ffmpeg audio overlay failed: %s", result.stderr[-500:])
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg audio overlay timed out (5 min)")
        return False
    except Exception as e:
        logger.error("ffmpeg audio overlay error: %s", e)
        return False


async def apply_audio_overlay(
    media_item_id: str,
    video_url: str,
    audio_url: str,
    volume_mix: float = 1.0,
    audio_start_offset: float = 0.0,
) -> dict:
    """Apply audio overlay to a video: download both, mix with ffmpeg, re-upload.

    Args:
        media_item_id: UUID string of the MediaItem (video) record
        video_url: URL of the video file
        audio_url: URL of the audio track file
        volume_mix: 0.0-1.0 blend ratio (1.0 = full replacement)
        audio_start_offset: Seconds into the audio track to start from

    Returns:
        dict with processing results
    """
    logger.info(
        "Applying audio overlay to video %s (volume_mix=%.2f)",
        media_item_id,
        volume_mix,
    )

    with tempfile.TemporaryDirectory(prefix="swimbuddz_audio_") as tmpdir:
        video_path = os.path.join(tmpdir, "video_input")
        audio_path = os.path.join(tmpdir, "audio_input")
        output_path = os.path.join(tmpdir, f"output_{uuid.uuid4()}.mp4")

        # Step 1: Download video
        logger.info("Downloading video from %s", video_url[:80])
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                async with client.stream("GET", video_url) as resp:
                    resp.raise_for_status()
                    with open(video_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
        except Exception as e:
            logger.error("Failed to download video: %s", e)
            await _mark_processed_with_error(
                media_item_id, f"video_download_failed: {e}"
            )
            return {"error": "video_download_failed"}

        # Step 2: Download audio track
        logger.info("Downloading audio from %s", audio_url[:80])
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                async with client.stream("GET", audio_url) as resp:
                    resp.raise_for_status()
                    with open(audio_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            f.write(chunk)
        except Exception as e:
            logger.error("Failed to download audio: %s", e)
            await _mark_processed_with_error(
                media_item_id, f"audio_download_failed: {e}"
            )
            return {"error": "audio_download_failed"}

        # Step 3: Apply audio overlay with ffmpeg
        success = _apply_audio_to_video(
            video_path, audio_path, output_path, volume_mix, audio_start_offset
        )

        if not success or not os.path.exists(output_path):
            logger.error("Audio overlay failed for %s", media_item_id)
            await _mark_processed_with_error(
                media_item_id, "audio_overlay_ffmpeg_failed"
            )
            return {"error": "audio_overlay_failed"}

        output_size = os.path.getsize(output_path)
        logger.info("Audio overlay complete: %d bytes", output_size)

        # Step 4: Upload result
        with open(output_path, "rb") as f:
            output_data = f.read()

        storage_name = f"product-videos/{uuid.uuid4()}_audio.mp4"
        new_file_url, _ = await storage_service.upload_media(
            output_data,
            storage_name,
            "video/mp4",
            bucket_type=BucketType.PUBLIC,
        )
        logger.info("Uploaded audio-overlaid video: %s", new_file_url[:80])

        # Step 5: Update database record
        async with AsyncSessionLocal() as db:
            query = select(MediaItem).where(MediaItem.id == media_item_id)
            result = await db.execute(query)
            item = result.scalar_one_or_none()

            if item:
                item.file_url = new_file_url
                item.is_processed = True
                # Merge audio overlay info into existing metadata
                existing_meta = item.metadata_info or {}
                existing_meta["audio_overlay"] = {
                    "volume_mix": volume_mix,
                    "audio_start_offset": audio_start_offset,
                    "output_size_bytes": output_size,
                }
                item.metadata_info = existing_meta
                await db.commit()
                logger.info("Updated MediaItem %s with audio overlay", media_item_id)

        return {"status": "ok", "new_file_url": new_file_url}
