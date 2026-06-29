"""Storage utilities for handling file uploads with Supabase/S3."""

import uuid
from enum import Enum
from io import BytesIO
from typing import Optional, Tuple
from urllib.parse import urlparse

from PIL import Image

from libs.common.config import get_settings
from libs.common.supabase import get_supabase_admin_client


class BucketType(str, Enum):
    """Bucket type for S3 storage."""

    PUBLIC = "public"
    PRIVATE = "private"


# Storage configuration
settings = get_settings()
STORAGE_BACKEND = getattr(settings, "STORAGE_BACKEND", "supabase")  # supabase or s3
SUPABASE_BUCKET = settings.SUPABASE_STORAGE_BUCKET

# S3 configuration - standardized bucket names
AWS_ACCESS_KEY = getattr(settings, "AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = getattr(settings, "AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = getattr(settings, "AWS_REGION", "eu-west-1")
AWS_BUCKET_PUBLIC = getattr(settings, "AWS_S3_BUCKET_PUBLIC", "")
AWS_BUCKET_PRIVATE = getattr(settings, "AWS_S3_BUCKET_PRIVATE", "")
CLOUDFRONT_URL = getattr(settings, "CLOUDFRONT_URL", "").rstrip("/")

# Map purposes to bucket types
PURPOSE_BUCKET_MAP = {
    # Public bucket - publicly accessible content
    "profile_photo": BucketType.PUBLIC,
    "cover_image": BucketType.PUBLIC,
    "content_image": BucketType.PUBLIC,
    "category_image": BucketType.PUBLIC,
    "collection_image": BucketType.PUBLIC,
    "product_image": BucketType.PUBLIC,
    "product_video": BucketType.PUBLIC,
    "size_chart": BucketType.PUBLIC,
    "general": BucketType.PUBLIC,
    "media": BucketType.PUBLIC,  # Gallery/album items
    "audio_track": BucketType.PUBLIC,  # Audio overlay tracks
    # Challenges (Phase 2 of the challenges revamp): public-bucket so the
    # public landing-page surface can render example media + winner proofs
    # without signed URL juggling.
    "challenge_example": BucketType.PUBLIC,
    "challenge_proof": BucketType.PUBLIC,
    "badge_image": BucketType.PUBLIC,
    # Private bucket - restricted access content
    "coach_document": BucketType.PRIVATE,
    "payment_proof": BucketType.PRIVATE,
    "milestone_evidence": BucketType.PRIVATE,
    "milestone_video": BucketType.PRIVATE,
    "strokelab_original": BucketType.PRIVATE,
    "strokelab_annotated": BucketType.PRIVATE,
    "strokelab_evidence": BucketType.PRIVATE,
    "strokelab_share": BucketType.PRIVATE,
}


def get_bucket_for_purpose(purpose: str) -> BucketType:
    """Determine which bucket to use based on upload purpose."""
    return PURPOSE_BUCKET_MAP.get(purpose, BucketType.PUBLIC)


class StorageService:
    """Abstract storage service for file uploads."""

    def __init__(self):
        self.backend = STORAGE_BACKEND

        if self.backend == "supabase":
            self.supabase = get_supabase_admin_client()
            self.bucket = SUPABASE_BUCKET
        elif self.backend == "s3":
            import boto3

            self.s3_client = boto3.client(
                "s3",
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                region_name=AWS_REGION,
            )
            self.bucket_public = AWS_BUCKET_PUBLIC
            self.bucket_private = AWS_BUCKET_PRIVATE

    def _get_s3_bucket(self, bucket_type: BucketType) -> str:
        """Get the appropriate S3 bucket name based on type."""
        if bucket_type == BucketType.PRIVATE:
            return self.bucket_private
        return self.bucket_public

    async def upload_media(
        self,
        file_data: bytes,
        filename: str,
        content_type: str = "image/jpeg",
        bucket_type: BucketType = BucketType.PUBLIC,
        *,
        preserve_filename: bool = False,
        generate_thumbnail: bool = True,
    ) -> Tuple[str, Optional[str]]:
        """
        Upload media (photo/video) and generate thumbnail if image.

        Args:
            file_data: The file bytes to upload
            filename: The filename/path to use for storage
            content_type: MIME type of the file
            bucket_type: Which bucket to use (PUBLIC or PRIVATE)
            preserve_filename: Store exactly at filename instead of UUID-renaming it
            generate_thumbnail: Generate image thumbnail where applicable

        Returns: (file_url, thumbnail_url)
        """
        if preserve_filename:
            unique_filename = filename
        else:
            # Generate unique filename
            file_ext = filename.split(".")[-1]
            unique_filename = f"{uuid.uuid4()}.{file_ext}"

            # Preserve directory structure if filename contains path
            if "/" in filename:
                # Keep the directory structure, just make the filename unique
                dir_path = "/".join(filename.split("/")[:-1])
                unique_filename = f"{dir_path}/{uuid.uuid4()}.{file_ext}"

        thumbnail_url = None

        # Only generate thumbnail for images
        if generate_thumbnail and content_type.startswith("image/"):
            file_ext = unique_filename.rsplit(".", 1)[-1]
            thumbnail_filename = (
                unique_filename.replace(f".{file_ext}", f"_thumb.{file_ext}")
                if "." in unique_filename
                else f"{unique_filename}_thumb"
            )
            thumbnail_data = self._create_thumbnail(file_data)

            if self.backend == "supabase":
                thumbnail_url = await self._upload_supabase(
                    thumbnail_filename, thumbnail_data, content_type
                )
            elif self.backend == "s3":
                thumbnail_url = await self._upload_s3(
                    thumbnail_filename, thumbnail_data, content_type, bucket_type
                )

        if self.backend == "supabase":
            # Upload to Supabase Storage
            file_url = await self._upload_supabase(
                unique_filename, file_data, content_type
            )
        elif self.backend == "s3":
            # Upload to S3
            file_url = await self._upload_s3(
                unique_filename, file_data, content_type, bucket_type
            )
        else:
            raise ValueError(f"Unknown storage backend: {self.backend}")

        return file_url, thumbnail_url

    def _create_thumbnail(
        self, image_data: bytes, size: Tuple[int, int] = (600, 600)
    ) -> bytes:
        """Create thumbnail from image data. Default 600x600 for good quality on album covers."""
        try:
            img = Image.open(BytesIO(image_data))
            img.thumbnail(size, Image.Resampling.LANCZOS)

            # Convert to bytes
            buffer = BytesIO()
            # Preserve format or default to JPEG
            fmt = img.format or "JPEG"
            img.save(buffer, format=fmt)
            return buffer.getvalue()
        except Exception:
            # Fallback if thumbnail creation fails
            return image_data

    async def _upload_supabase(
        self, filename: str, data: bytes, content_type: str
    ) -> str:
        """Upload to Supabase Storage."""
        path = f"media/{filename}"  # Changed folder to media

        self.supabase.storage.from_(self.bucket).upload(
            path=path, file=data, file_options={"content-type": content_type}
        )

        # Get public URL
        url_response = self.supabase.storage.from_(self.bucket).get_public_url(path)
        return url_response

    async def _upload_s3(
        self,
        filename: str,
        data: bytes,
        content_type: str,
        bucket_type: BucketType = BucketType.PUBLIC,
    ) -> str:
        """Upload to S3."""
        bucket = self._get_s3_bucket(bucket_type)

        self.s3_client.put_object(
            Bucket=bucket, Key=filename, Body=data, ContentType=content_type
        )

        # For public bucket, prefer CloudFront if configured
        if bucket_type == BucketType.PUBLIC and CLOUDFRONT_URL:
            return f"{CLOUDFRONT_URL}/{filename}"

        # For private bucket or if no CloudFront, return S3 URL
        # Note: Private bucket files will need signed URLs for access
        return f"https://{bucket}.s3.{AWS_REGION}.amazonaws.com/{filename}"

    async def delete_media(
        self,
        file_url: str,
        thumbnail_url: Optional[str] = None,
        bucket_type: Optional[BucketType] = None,
        *,
        is_key: bool = False,
    ):
        """Delete media and thumbnail from storage.

        ``file_url`` is the historical public/private URL by default. Internal
        callers that already own an object key can pass ``is_key=True``.
        """
        if self.backend == "supabase":
            # Extract path from URL
            # URL format: .../storage/v1/object/public/bucket/media/filename
            try:
                path = file_url if is_key else file_url.split(f"{self.bucket}/")[-1]
                self.supabase.storage.from_(self.bucket).remove([path])

                if thumbnail_url:
                    thumb_path = (
                        thumbnail_url
                        if is_key
                        else thumbnail_url.split(f"{self.bucket}/")[-1]
                    )
                    self.supabase.storage.from_(self.bucket).remove([thumb_path])
            except Exception:
                pass  # Ignore errors during deletion

        elif self.backend == "s3":
            try:
                # Determine bucket from URL if not specified
                bucket = None
                if bucket_type:
                    bucket = self._get_s3_bucket(bucket_type)
                else:
                    # Try to determine bucket from URL
                    if self.bucket_private and self.bucket_private in file_url:
                        bucket = self.bucket_private
                    else:
                        bucket = self.bucket_public

                # Extract key from URL path (works for S3 and CloudFront)
                key = file_url if is_key else urlparse(file_url).path.lstrip("/")
                if key:
                    self.s3_client.delete_object(Bucket=bucket, Key=key)

                if thumbnail_url:
                    thumb_key = (
                        thumbnail_url
                        if is_key
                        else urlparse(thumbnail_url).path.lstrip("/")
                    )
                    if thumb_key:
                        self.s3_client.delete_object(Bucket=bucket, Key=thumb_key)
            except Exception:
                pass

    async def generate_presigned_url(
        self,
        file_key: str,
        bucket_type: BucketType = BucketType.PRIVATE,
        expiration: int = 3600,
        *,
        operation: str = "get_object",
        content_type: Optional[str] = None,
    ) -> str:
        """
        Generate a presigned URL for accessing or uploading private files.

        Args:
            file_key: The S3 object key
            bucket_type: Which bucket the file is in
            expiration: URL expiration time in seconds (default 1 hour)
            operation: S3 operation to sign ("get_object" or "put_object")
            content_type: Required for "put_object" browser uploads

        Returns: Presigned URL string
        """
        if self.backend != "s3":
            raise ValueError("Presigned URLs are only supported for S3 backend")
        if operation not in {"get_object", "put_object"}:
            raise ValueError("Unsupported presigned URL operation")

        bucket = self._get_s3_bucket(bucket_type)
        params = {"Bucket": bucket, "Key": file_key}
        if operation == "put_object":
            params["ContentType"] = content_type or "application/octet-stream"
        kwargs = {"Params": params, "ExpiresIn": expiration}
        if operation == "put_object":
            kwargs["HttpMethod"] = "PUT"
        return self.s3_client.generate_presigned_url(operation, **kwargs)

    async def head_object(
        self,
        file_key: str,
        bucket_type: BucketType = BucketType.PRIVATE,
    ) -> dict:
        """Return metadata for an object owned by the media service."""
        if self.backend == "s3":
            bucket = self._get_s3_bucket(bucket_type)
            resp = self.s3_client.head_object(Bucket=bucket, Key=file_key)
            return {
                "object_key": file_key,
                "bucket_type": bucket_type.value,
                "size_bytes": int(resp.get("ContentLength") or 0),
                "content_type": resp.get("ContentType"),
                "etag": str(resp.get("ETag") or "").strip('"') or None,
            }

        try:
            data = self.supabase.storage.from_(self.bucket).download(file_key)
        except Exception as exc:
            raise FileNotFoundError(file_key) from exc
        return {
            "object_key": file_key,
            "bucket_type": bucket_type.value,
            "size_bytes": len(data),
            "content_type": None,
            "etag": None,
        }


# Singleton instance
storage_service = StorageService()
