"""Background ZIP exports for full-quality vault originals."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import dispatch_notification, get_member_by_auth_id
from libs.db.config import AsyncSessionLocal
from services.media_service.models import (
    MediaItem,
    MediaTransferLog,
    MediaVault,
    MediaVaultExport,
)
from services.media_service.services.storage import (
    BucketType,
    attachment_content_disposition,
    recommended_multipart_part_size,
    storage_service,
)

logger = get_logger(__name__)
S3_MAX_OBJECT_BYTES = 5 * 1024**4


class S3MultipartArchiveWriter:
    """Unseekable ZIP target that uploads bounded chunks directly to S3."""

    def __init__(
        self,
        *,
        client,
        bucket: str,
        key: str,
        download_name: str,
        part_size: int = 64 * 1024**2,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.key = key
        self.part_size = part_size
        self.buffer = bytearray()
        self.parts: list[dict[str, object]] = []
        self.position = 0
        self.completed = False
        response = self.client.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ContentType="application/zip",
            ContentDisposition=attachment_content_disposition(download_name),
            ServerSideEncryption="AES256",
        )
        self.upload_id = str(response["UploadId"])

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, data: bytes) -> int:
        if not data:
            return 0
        if self.position + len(data) > S3_MAX_OBJECT_BYTES:
            raise ValueError("ZIP export exceeds S3's five TiB object limit")
        self.buffer.extend(data)
        self.position += len(data)
        while len(self.buffer) >= self.part_size:
            self._upload(bytes(self.buffer[: self.part_size]))
            del self.buffer[: self.part_size]
        return len(data)

    def flush(self) -> None:
        """ZipFile calls flush; S3 parts flush only at the configured boundary."""

    def _upload(self, data: bytes) -> None:
        part_number = len(self.parts) + 1
        if part_number > 10_000:
            raise ValueError("ZIP export exceeds S3's 10,000-part limit")
        response = self.client.upload_part(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            PartNumber=part_number,
            Body=data,
        )
        self.parts.append(
            {
                "PartNumber": part_number,
                "ETag": response["ETag"],
            }
        )

    def complete(self) -> int:
        if self.completed:
            return self.position
        if self.buffer:
            self._upload(bytes(self.buffer))
            self.buffer.clear()
        self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            MultipartUpload={"Parts": self.parts},
        )
        self.completed = True
        return self.position

    def abort(self) -> None:
        if self.completed:
            return
        self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
        )


def _social_dimensions(preset: str) -> tuple[int, int]:
    return (1080, 1080) if preset == "social-square" else (1080, 1350)


def _build_social_derivative(
    *,
    input_path: str,
    output_path: str,
    media_type: str,
    preset: str,
) -> None:
    width, height = _social_dimensions(preset)
    if media_type == "IMAGE":
        from PIL import Image, ImageOps

        try:
            with Image.open(input_path) as image:
                image = ImageOps.exif_transpose(image)
                image = ImageOps.fit(
                    image,
                    (width, height),
                    method=Image.Resampling.LANCZOS,
                )
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(output_path, "JPEG", quality=90, optimize=True)
        except Exception:
            converted = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    input_path,
                    "-vf",
                    (
                        f"scale={width}:{height}:"
                        f"force_original_aspect_ratio=increase,crop={width}:{height}"
                    ),
                    "-frames:v",
                    "1",
                    output_path,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            if converted.returncode != 0:
                raise RuntimeError(
                    f"social image export failed: {converted.stderr[-800:]}"
                )
        return
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            ),
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            output_path,
        ],
        capture_output=True,
        text=True,
        timeout=7200,
    )
    if result.returncode != 0:
        raise RuntimeError(f"social video export failed: {result.stderr[-800:]}")


async def build_vault_export(export_id: str) -> dict:
    """Build an export without loading multi-gigabyte originals into memory."""
    export_uuid = uuid.UUID(export_id)
    async with AsyncSessionLocal() as db:
        export = await db.get(MediaVaultExport, export_uuid)
        if not export:
            return {"status": "missing"}
        if export.status == "ready":
            return {"status": "ready", "size_bytes": export.size_bytes}
        export.status = "processing"
        await db.commit()

        vault = await db.get(MediaVault, export.vault_id)
        item_ids = [uuid.UUID(value) for value in export.media_item_ids]
        rows = await db.execute(
            select(MediaItem).where(
                MediaItem.vault_id == export.vault_id,
                MediaItem.id.in_(item_ids),
                MediaItem.processing_status == "ready",
                MediaItem.soft_deleted_at.is_(None),
            )
        )
        items_by_id = {item.id: item for item in rows.scalars().all()}
        items = [items_by_id[item_id] for item_id in item_ids if item_id in items_by_id]

    try:
        object_key = (
            f"vault-exports/{export.vault_id}/{export.id}/" f"originals-{export.id}.zip"
        )
        download_name = (
            f"{vault.capture_date.isoformat()}-{vault.title}.zip"
            if vault
            else f"{export.vault_id}.zip"
        )
        writer = S3MultipartArchiveWriter(
            client=storage_service.s3_client,
            bucket=storage_service.bucket_private,
            key=object_key,
            download_name=download_name,
            part_size=max(
                64 * 1024**2,
                recommended_multipart_part_size(
                    min(
                        S3_MAX_OBJECT_BYTES,
                        sum(int(item.size_bytes or 0) for item in items) + 1024**2,
                    )
                ),
            ),
        )
        try:
            with (
                tempfile.TemporaryDirectory() as work_dir,
                zipfile.ZipFile(
                    writer,
                    mode="w",
                    compression=zipfile.ZIP_STORED,
                    allowZip64=True,
                ) as archive,
            ):
                for index, item in enumerate(items, start=1):
                    filename = item.original_filename or f"{item.id}"
                    if export.preset == "original":
                        archive_name = f"{index:04d}-{filename}"
                        response = storage_service.s3_client.get_object(
                            Bucket=storage_service.bucket_private,
                            Key=item.object_key,
                        )
                        body = response["Body"]
                        try:
                            with archive.open(
                                archive_name, mode="w", force_zip64=True
                            ) as out:
                                while chunk := body.read(8 * 1024**2):
                                    out.write(chunk)
                        finally:
                            body.close()
                        continue

                    extension = Path(filename).suffix or ".bin"
                    input_path = str(Path(work_dir) / f"{item.id}{extension}")
                    media_type = (
                        item.media_type.value
                        if hasattr(item.media_type, "value")
                        else str(item.media_type)
                    )
                    output_extension = ".jpg" if media_type == "IMAGE" else ".mp4"
                    output_path = str(
                        Path(work_dir) / f"{item.id}-{export.preset}{output_extension}"
                    )
                    storage_service.s3_client.download_file(
                        storage_service.bucket_private,
                        item.object_key,
                        input_path,
                    )
                    _build_social_derivative(
                        input_path=input_path,
                        output_path=output_path,
                        media_type=media_type,
                        preset=export.preset,
                    )
                    archive.write(
                        output_path,
                        arcname=(
                            f"{index:04d}-{Path(filename).stem}-"
                            f"{export.preset}{output_extension}"
                        ),
                    )
                    Path(input_path).unlink(missing_ok=True)
                    Path(output_path).unlink(missing_ok=True)

                manifest = (
                    "SwimBuddz Media Vault export\n"
                    f"Vault: {vault.title if vault else export.vault_id}\n"
                    f"Preset: {export.preset}\n"
                    "Source originals remain byte-for-byte preserved in the "
                    "private vault. Social files are explicit derivatives.\n"
                )
                archive.writestr("README.txt", manifest)
            size_bytes = writer.complete()
        except Exception:
            writer.abort()
            raise

        async with AsyncSessionLocal() as db:
            export = await db.get(MediaVaultExport, export_uuid)
            if not export:
                return {"status": "missing-after-build"}
            export.object_key = object_key
            export.size_bytes = size_bytes
            export.status = "ready"
            export.completed_at = utc_now()
            await db.commit()
            member = await get_member_by_auth_id(
                str(export.requested_by), calling_service="media"
            )
            await dispatch_notification(
                type="media_vault_export_ready",
                category="media",
                member_ids=[str(member["id"])] if member else [],
                title="Your media export is ready",
                body="The full-quality ZIP is available for 24 hours.",
                action_url=f"/admin/media-vault/{export.vault_id}",
                calling_service="media",
                metadata={
                    "requested_by_auth_id": str(export.requested_by),
                    "export_id": str(export.id),
                },
            )
        return {"status": "ready", "size_bytes": size_bytes}
    except Exception as exc:
        logger.exception("Vault export %s failed", export_id)
        async with AsyncSessionLocal() as db:
            export = await db.get(MediaVaultExport, export_uuid)
            if export:
                export.status = "failed"
                export.error_message = str(exc)[:2000]
                await db.commit()
        raise


async def cleanup_expired_vault_exports() -> dict:
    """Expire generated ZIPs and release stale multipart reservations."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(MediaVaultExport).where(
                MediaVaultExport.expires_at < utc_now(),
                MediaVaultExport.object_key.is_not(None),
                MediaVaultExport.status == "ready",
            )
        )
        exports = list(rows.scalars().all())
        deleted = 0
        for export in exports:
            await storage_service.delete_media(
                export.object_key,
                bucket_type=BucketType.PRIVATE,
                is_key=True,
            )
            export.object_key = None
            export.status = "expired"
            deleted += 1
        stale_rows = await db.execute(
            select(MediaItem).where(
                MediaItem.processing_status == "uploading",
                MediaItem.created_at < utc_now() - timedelta(days=2),
            )
        )
        stale_items = list(stale_rows.scalars().all())
        for item in stale_items:
            if item.multipart_upload_id and item.object_key:
                try:
                    await storage_service.abort_multipart_upload(
                        file_key=item.object_key,
                        upload_id=item.multipart_upload_id,
                    )
                except Exception:
                    pass
            item.multipart_upload_id = None
            item.processing_status = "aborted"
            transfer = await db.scalar(
                select(MediaTransferLog)
                .where(
                    MediaTransferLog.media_item_id == item.id,
                    MediaTransferLog.direction == "upload",
                    MediaTransferLog.status == "authorized",
                )
                .limit(1)
            )
            if transfer:
                transfer.status = "expired"
                transfer.completed_at = utc_now()
        await db.commit()
    return {
        "expired_exports": deleted,
        "expired_multipart_uploads": len(stale_items),
    }
