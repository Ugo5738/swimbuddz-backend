"""Storage utilities for handling file uploads with Supabase/S3."""

import os
import uuid
from io import BytesIO
from typing import Optional, Tuple

from PIL import Image

from supabase import Client, create_client

# Storage configuration
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "supabase")  # supabase or s3
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "swimbuddz-media")

# S3 configuration (fallback)
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_BUCKET = os.getenv("AWS_S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


class StorageService:
    """Abstract storage service for file uploads."""

    def __init__(self):
        self.backend = STORAGE_BACKEND

        if self.backend == "supabase":
            self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            self.bucket = SUPABASE_BUCKET
        elif self.backend == "s3":
            import boto3

            self.s3_client = boto3.client(
                "s3",
                aws_access_key_id=AWS_ACCESS_KEY,
                aws_secret_access_key=AWS_SECRET_KEY,
                region_name=AWS_REGION,
            )
            self.bucket = AWS_BUCKET

    async def upload_media(
        self, file_data: bytes, filename: str, content_type: str = "image/jpeg"
    ) -> Tuple[str, Optional[str]]:
        """
        Upload media (photo/video) and generate thumbnail if image.
        Returns: (file_url, thumbnail_url)
        """
        # Generate unique filename
        file_ext = filename.split(".")[-1]
        unique_filename = f"{uuid.uuid4()}.{file_ext}"

        thumbnail_url = None

        # Only generate thumbnail for images
        if content_type.startswith("image/"):
            thumbnail_filename = f"thumb_{unique_filename}"
            thumbnail_data = self._create_thumbnail(file_data)

            if self.backend == "supabase":
                thumbnail_url = await self._upload_supabase(
                    thumbnail_filename, thumbnail_data, content_type
                )
            elif self.backend == "s3":
                thumbnail_url = await self._upload_s3(
                    thumbnail_filename, thumbnail_data, content_type
                )

        if self.backend == "supabase":
            # Upload to Supabase Storage
            file_url = await self._upload_supabase(
                unique_filename, file_data, content_type
            )
        elif self.backend == "s3":
            # Upload to S3
            file_url = await self._upload_s3(unique_filename, file_data, content_type)
        else:
            raise ValueError(f"Unknown storage backend: {self.backend}")

        return file_url, thumbnail_url

    def _create_thumbnail(
        self, image_data: bytes, size: Tuple[int, int] = (300, 300)
    ) -> bytes:
        """Create thumbnail from image data."""
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

    async def _upload_s3(self, filename: str, data: bytes, content_type: str) -> str:
        """Upload to S3."""
        path = f"media/{filename}"

        self.s3_client.put_object(
            Bucket=self.bucket, Key=path, Body=data, ContentType=content_type
        )

        # Generate public URL
        url = f"https://{self.bucket}.s3.{AWS_REGION}.amazonaws.com/{path}"
        return url

    async def delete_media(self, file_url: str, thumbnail_url: Optional[str] = None):
        """Delete media and thumbnail from storage."""
        if self.backend == "supabase":
            # Extract path from URL
            # URL format: .../storage/v1/object/public/bucket/media/filename
            try:
                path = file_url.split(f"{self.bucket}/")[-1]
                self.supabase.storage.from_(self.bucket).remove([path])

                if thumbnail_url:
                    thumb_path = thumbnail_url.split(f"{self.bucket}/")[-1]
                    self.supabase.storage.from_(self.bucket).remove([thumb_path])
            except Exception:
                pass  # Ignore errors during deletion

        elif self.backend == "s3":
            try:
                # Extract key from URL
                key = file_url.split(".com/")[-1]
                self.s3_client.delete_object(Bucket=self.bucket, Key=key)

                if thumbnail_url:
                    thumb_key = thumbnail_url.split(".com/")[-1]
                    self.s3_client.delete_object(Bucket=self.bucket, Key=thumb_key)
            except Exception:
                pass


# Singleton instance
storage_service = StorageService()
