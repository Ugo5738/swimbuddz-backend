"""Safeguards for automated historical vault preview processing."""

from datetime import datetime, timedelta, timezone

from services.media_service.tasks.vault_preview_backfill import (
    MAX_ATTEMPTS,
    RETRY_AFTER,
    STALE_AFTER,
    preview_needs_backfill,
)


def test_new_video_without_proxy_is_eligible():
    assert preview_needs_backfill({}, datetime.now(timezone.utc))


def test_active_user_requested_job_not_duplicated():
    now = datetime.now(timezone.utc)
    assert not preview_needs_backfill({"proxy_status": "pending"}, now)
    assert not preview_needs_backfill({"proxy_status": "processing"}, now)


def test_recent_auto_job_is_not_duplicated():
    now = datetime.now(timezone.utc)
    metadata = {"proxy_status": "pending", "auto_preview_queued_at": now.isoformat()}
    assert not preview_needs_backfill(metadata, now)


def test_stale_auto_job_is_recovered():
    now = datetime.now(timezone.utc)
    metadata = {
        "proxy_status": "processing",
        "auto_preview_queued_at": (now - STALE_AFTER - timedelta(minutes=1)).isoformat(),
        "auto_preview_attempts": 1,
    }
    assert preview_needs_backfill(metadata, now)


def test_failed_job_respects_retry_window():
    now = datetime.now(timezone.utc)
    metadata = {
        "proxy_status": "failed",
        "auto_preview_queued_at": (now - RETRY_AFTER / 2).isoformat(),
    }
    assert not preview_needs_backfill(metadata, now)
    metadata["auto_preview_queued_at"] = (now - RETRY_AFTER - timedelta(seconds=1)).isoformat()
    assert preview_needs_backfill(metadata, now)


def test_exhausted_attempts_stop_automatic_retries():
    now = datetime.now(timezone.utc)
    assert not preview_needs_backfill(
        {"proxy_status": "failed", "auto_preview_attempts": MAX_ATTEMPTS}, now
    )
