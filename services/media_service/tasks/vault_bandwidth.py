"""Reconcile vault download authorizations with S3 server access logs.

S3 writes delivery logs asynchronously. The worker scans a bounded date
lookback, records every source log object and request idempotently, and replaces
application estimates with the bytes S3 says it actually sent.
"""

from __future__ import annotations

import gzip
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import unquote

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.media_service.models import (
    MediaAccessLogEvent,
    MediaAccessLogObject,
    MediaItem,
    MediaTransferLog,
    MediaVaultExport,
)
from services.media_service.services.storage import storage_service

logger = get_logger(__name__)
settings = get_settings()

VAULT_OBJECT_PREFIXES = ("vaults/", "vault-exports/", "vault-derivatives/")
S3_DOWNLOAD_OPERATIONS = {"REST.GET.OBJECT", "REST.GET.OBJECT_VERSION"}
S3_DOWNLOAD_STATUS_CODES = {200, 206}


@dataclass(frozen=True)
class S3AccessEvent:
    target_bucket: str
    request_id: str
    occurred_at: datetime
    object_key: str
    bytes_sent: int
    status_code: int
    remote_ip: Optional[str]
    user_agent: Optional[str]
    operation: str
    requester: Optional[str]
    error_code: Optional[str]
    authentication_type: Optional[str]


def parse_s3_access_log_line(line: str) -> Optional[S3AccessEvent]:
    """Parse one successful vault GET from the S3 server-access-log format."""
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    try:
        fields = shlex.split(line)
    except ValueError:
        return None
    if len(fields) < 18:
        return None

    operation = fields[7]
    raw_key = fields[8]
    if operation not in S3_DOWNLOAD_OPERATIONS or raw_key == "-":
        return None
    object_key = unquote(raw_key)
    if not object_key.startswith(VAULT_OBJECT_PREFIXES):
        return None
    try:
        status_code = int(fields[10])
        bytes_sent = int(fields[12]) if fields[12] != "-" else 0
        occurred_at = datetime.strptime(
            f"{fields[2].lstrip('[')} {fields[3].rstrip(']')}",
            "%d/%b/%Y:%H:%M:%S %z",
        )
    except (TypeError, ValueError):
        return None
    if status_code not in S3_DOWNLOAD_STATUS_CODES or bytes_sent <= 0:
        return None
    authentication_type = fields[22] if len(fields) > 22 and fields[22] != "-" else None
    if authentication_type and authentication_type != "QueryString":
        return None

    return S3AccessEvent(
        target_bucket=fields[1],
        request_id=fields[6],
        occurred_at=occurred_at,
        object_key=object_key,
        bytes_sent=bytes_sent,
        status_code=status_code,
        remote_ip=None if fields[4] == "-" else fields[4],
        requester=None if fields[5] == "-" else fields[5],
        operation=operation,
        error_code=None if fields[11] == "-" else fields[11],
        user_agent=None if fields[17] == "-" else fields[17][:512],
        authentication_type=authentication_type,
    )


def _decode_log_object(raw: bytes, *, object_key: str, content_encoding: str) -> str:
    if object_key.endswith(".gz") or content_encoding.lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _candidate_date_prefixes(now: datetime) -> list[str]:
    prefix = settings.MEDIA_VAULT_S3_ACCESS_LOG_PREFIX.strip("/")
    prefix = f"{prefix}/" if prefix else ""
    lookback = max(1, settings.MEDIA_VAULT_ACCESS_LOG_LOOKBACK_DAYS)
    return [
        f"{prefix}{(now - timedelta(days=offset)).strftime('%Y-%m-%d')}-"
        for offset in reversed(range(lookback))
    ]


def _list_log_objects(client: Any, bucket: str, now: datetime) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_objects_v2")
    for prefix in _candidate_date_prefixes(now):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects.extend(page.get("Contents", []))
    objects.sort(key=lambda value: str(value.get("Key") or ""))
    return objects


async def _find_transfer(
    db: AsyncSession, event: S3AccessEvent
) -> Optional[MediaTransferLog]:
    ttl = max(60, settings.MEDIA_VAULT_SIGNED_URL_TTL_SECONDS)
    direct_rows = await db.execute(
        select(MediaTransferLog)
        .where(
            MediaTransferLog.direction == "download",
            MediaTransferLog.delivery_method == "s3_presigned",
            MediaTransferLog.object_key == event.object_key,
            MediaTransferLog.created_at >= event.occurred_at - timedelta(seconds=ttl),
            MediaTransferLog.created_at <= event.occurred_at + timedelta(minutes=5),
        )
        .order_by(MediaTransferLog.created_at.desc())
        .limit(30)
    )
    candidates = list(direct_rows.scalars().all())

    # Ledger rows created before object_key was denormalized still reconcile
    # through their media/export foreign keys.
    if not candidates:
        candidates = await _find_legacy_transfer_candidates(db, event, ttl)
    if not candidates:
        return None

    def score(transfer: MediaTransferLog) -> tuple[int, datetime]:
        identity_score = 0
        if event.remote_ip and transfer.ip_address == event.remote_ip:
            identity_score += 2
        if event.user_agent and transfer.user_agent == event.user_agent:
            identity_score += 1
        return identity_score, transfer.created_at

    return max(candidates, key=score)


async def _find_legacy_transfer_candidates(
    db: AsyncSession, event: S3AccessEvent, ttl: int
) -> list[MediaTransferLog]:
    item_id = await db.scalar(
        select(MediaItem.id).where(MediaItem.object_key == event.object_key).limit(1)
    )
    export_id = await db.scalar(
        select(MediaVaultExport.id)
        .where(MediaVaultExport.object_key == event.object_key)
        .limit(1)
    )
    if not item_id and not export_id:
        return []

    object_filters = []
    if item_id:
        object_filters.append(MediaTransferLog.media_item_id == item_id)
    if export_id:
        object_filters.append(MediaTransferLog.export_id == export_id)
    rows = await db.execute(
        select(MediaTransferLog)
        .where(
            MediaTransferLog.direction == "download",
            MediaTransferLog.delivery_method == "s3_presigned",
            or_(*object_filters),
            MediaTransferLog.created_at >= event.occurred_at - timedelta(seconds=ttl),
            MediaTransferLog.created_at <= event.occurred_at + timedelta(minutes=5),
        )
        .order_by(MediaTransferLog.created_at.desc())
        .limit(30)
    )
    return list(rows.scalars().all())


async def _link_event_to_transfer(
    db: AsyncSession,
    access_event: MediaAccessLogEvent,
    transfer: MediaTransferLog,
) -> None:
    access_event.transfer_log_id = transfer.id
    access_event.match_status = "matched"
    await db.flush()
    actual_bytes = await db.scalar(
        select(func.coalesce(func.sum(MediaAccessLogEvent.bytes_sent), 0)).where(
            MediaAccessLogEvent.transfer_log_id == transfer.id,
            MediaAccessLogEvent.match_status == "matched",
        )
    )
    event_count = await db.scalar(
        select(func.count(MediaAccessLogEvent.id)).where(
            MediaAccessLogEvent.transfer_log_id == transfer.id,
            MediaAccessLogEvent.match_status == "matched",
        )
    )
    transfer.status = "completed"
    transfer.bytes_transferred = int(actual_bytes or 0)
    transfer.measurement_source = "s3_access_log"
    transfer.completed_at = max(
        filter(None, (transfer.completed_at, access_event.occurred_at)),
        default=access_event.occurred_at,
    )
    metadata = dict(transfer.metadata_json or {})
    metadata["access_log_event_count"] = int(event_count or 0)
    metadata["last_aws_request_id"] = access_event.request_id
    transfer.metadata_json = metadata


async def _ingest_log_object(
    db: AsyncSession,
    *,
    client: Any,
    source_bucket: str,
    object_summary: dict[str, Any],
) -> tuple[int, int]:
    object_key = str(object_summary["Key"])
    record = await db.scalar(
        select(MediaAccessLogObject)
        .where(
            MediaAccessLogObject.provider == "s3",
            MediaAccessLogObject.source_bucket == source_bucket,
            MediaAccessLogObject.object_key == object_key,
        )
        .with_for_update()
    )
    if record and record.status == "completed":
        return 0, 0
    if not record:
        record = MediaAccessLogObject(
            provider="s3",
            source_bucket=source_bucket,
            object_key=object_key,
            etag=str(object_summary.get("ETag") or "").strip('"') or None,
        )
        db.add(record)
        await db.flush()
    else:
        record.status = "processing"
        record.error_message = None
    # Persist the claim so a parsing/download failure remains visible and can
    # be retried on the next scheduled run.
    await db.commit()

    try:
        response = client.get_object(Bucket=source_bucket, Key=object_key)
        body = response["Body"]
        try:
            raw = body.read()
        finally:
            body.close()
        text = _decode_log_object(
            raw,
            object_key=object_key,
            content_encoding=str(response.get("ContentEncoding") or ""),
        )
        parsed = [
            event
            for line in text.splitlines()
            if (event := parse_s3_access_log_line(line)) is not None
        ]
        request_ids = [event.request_id for event in parsed]
        existing_requests: set[tuple[str, str]] = set()
        if request_ids:
            rows = await db.execute(
                select(
                    MediaAccessLogEvent.target_bucket,
                    MediaAccessLogEvent.request_id,
                ).where(
                    MediaAccessLogEvent.provider == "s3",
                    MediaAccessLogEvent.request_id.in_(request_ids),
                )
            )
            existing_requests = set(rows.all())

        ingested = 0
        matched = 0
        for event in parsed:
            request_key = (event.target_bucket, event.request_id)
            if request_key in existing_requests:
                continue
            transfer = await _find_transfer(db, event)
            access_event = MediaAccessLogEvent(
                source_log_object_id=record.id,
                transfer_log_id=transfer.id if transfer else None,
                provider="s3",
                target_bucket=event.target_bucket,
                request_id=event.request_id,
                occurred_at=event.occurred_at,
                object_key=event.object_key,
                bytes_sent=event.bytes_sent,
                status_code=event.status_code,
                match_status="matched" if transfer else "unmatched",
                remote_ip=event.remote_ip,
                user_agent=event.user_agent,
                metadata_json={
                    "operation": event.operation,
                    "requester": event.requester,
                    "error_code": event.error_code,
                    "authentication_type": event.authentication_type,
                },
            )
            db.add(access_event)
            await db.flush()
            existing_requests.add(request_key)
            ingested += 1
            if transfer:
                await _link_event_to_transfer(db, access_event, transfer)
                matched += 1

        record.status = "completed"
        record.event_count = ingested
        record.matched_event_count = matched
        record.processed_at = utc_now()
        await db.commit()
        return ingested, matched
    except Exception as exc:
        logger.exception("Failed to reconcile media access log %s", object_key)
        await db.rollback()
        record = await db.scalar(
            select(MediaAccessLogObject).where(
                MediaAccessLogObject.provider == "s3",
                MediaAccessLogObject.source_bucket == source_bucket,
                MediaAccessLogObject.object_key == object_key,
            )
        )
        if record:
            record.status = "failed"
            record.error_message = str(exc)[:2000]
            record.processed_at = utc_now()
            await db.commit()
        return 0, 0


async def _retry_unmatched_events(db: AsyncSession, now: datetime) -> int:
    lookback = max(1, settings.MEDIA_VAULT_ACCESS_LOG_LOOKBACK_DAYS)
    rows = await db.execute(
        select(MediaAccessLogEvent)
        .where(
            MediaAccessLogEvent.provider == "s3",
            MediaAccessLogEvent.match_status == "unmatched",
            MediaAccessLogEvent.occurred_at >= now - timedelta(days=lookback),
        )
        .order_by(MediaAccessLogEvent.occurred_at.desc())
        .limit(500)
    )
    matched = 0
    for access_event in rows.scalars().all():
        event = S3AccessEvent(
            target_bucket=access_event.target_bucket,
            request_id=access_event.request_id,
            occurred_at=access_event.occurred_at,
            object_key=access_event.object_key,
            bytes_sent=access_event.bytes_sent,
            status_code=access_event.status_code,
            remote_ip=access_event.remote_ip,
            user_agent=access_event.user_agent,
            operation=str((access_event.metadata_json or {}).get("operation") or ""),
            requester=(access_event.metadata_json or {}).get("requester"),
            error_code=(access_event.metadata_json or {}).get("error_code"),
            authentication_type=(access_event.metadata_json or {}).get(
                "authentication_type"
            ),
        )
        transfer = await _find_transfer(db, event)
        if transfer:
            await _link_event_to_transfer(db, access_event, transfer)
            matched += 1
    if matched:
        await db.commit()
    return matched


async def reconcile_vault_bandwidth() -> dict[str, int | str]:
    """Consume recent S3 logs and update the vault transfer ledger."""
    source_bucket = settings.MEDIA_VAULT_ACCESS_LOG_BUCKET.strip()
    if not source_bucket:
        return {"status": "disabled", "processed_objects": 0}
    if settings.STORAGE_BACKEND != "s3":
        return {"status": "not_s3", "processed_objects": 0}

    now = utc_now().astimezone(timezone.utc)
    candidates = _list_log_objects(storage_service.s3_client, source_bucket, now)
    max_objects = max(1, settings.MEDIA_VAULT_ACCESS_LOG_MAX_OBJECTS_PER_RUN)
    processed = 0
    events = 0
    matched = 0

    async with AsyncSessionLocal() as db:
        candidate_keys = [str(value.get("Key") or "") for value in candidates]
        completed_keys: set[str] = set()
        if candidate_keys:
            rows = await db.execute(
                select(MediaAccessLogObject.object_key).where(
                    MediaAccessLogObject.provider == "s3",
                    MediaAccessLogObject.source_bucket == source_bucket,
                    MediaAccessLogObject.status == "completed",
                    MediaAccessLogObject.object_key.in_(candidate_keys),
                )
            )
            completed_keys = set(rows.scalars().all())
        pending = [
            value
            for value in candidates
            if str(value.get("Key") or "") not in completed_keys
        ]
        for object_summary in pending[:max_objects]:
            ingested, object_matched = await _ingest_log_object(
                db,
                client=storage_service.s3_client,
                source_bucket=source_bucket,
                object_summary=object_summary,
            )
            processed += 1
            events += ingested
            matched += object_matched
        matched += await _retry_unmatched_events(db, now)

    result: dict[str, int | str] = {
        "status": "completed",
        "processed_objects": processed,
        "events": events,
        "matched": matched,
        "pending_log_objects": max(0, len(pending) - max_objects),
    }
    logger.info("Vault bandwidth reconciliation completed: %s", result)
    return result
