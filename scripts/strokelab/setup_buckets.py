"""Idempotent setup for the two Supabase storage buckets Stroke Lab needs.

  * strokelab-uploads   — raw member uploads (private, 50 MB cap, mp4/mov)
  * strokelab-annotated — worker-produced overlay videos (private, 60 MB cap)

Run once per environment after first deploy:

    ENV_FILE=.env.dev   python scripts/strokelab/setup_buckets.py
    ENV_FILE=.env.prod  python scripts/strokelab/setup_buckets.py

Re-runs are safe: existing buckets are left as-is unless --update is
passed, in which case file_size_limit + allowed_mime_types are pushed
to match what's here. Default behavior never deletes anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# scripts/strokelab/setup_buckets.py → backend root is two levels up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

env_file = os.environ.get("ENV_FILE", ".env.dev")
load_dotenv(PROJECT_ROOT / env_file, override=True)

from libs.common.supabase import get_supabase_admin_client  # noqa: E402

# Keep BUCKET ids in lock-step with services/ai_service/analysis/storage.py.
# When you change one, change the other (or wire the names through env vars
# in both places). Mismatch = silent uploads-into-the-void.
UPLOADS_BUCKET_ID = "strokelab-uploads"
ANNOTATED_BUCKET_ID = "strokelab-annotated"

# Tight mime allow-list: phones produce mp4/mov, occasionally hevc inside an
# mp4. Reject everything else at the storage layer so a malformed body can't
# reach the worker.
VIDEO_MIMES = [
    "video/mp4",
    "video/quicktime",
    "video/x-m4v",
    "video/webm",
]

# 50 MB matches the API-layer MAX_UPLOAD_BYTES in routers/analyze.py.
UPLOADS_OPTIONS = {
    "public": False,
    "file_size_limit": 50 * 1024 * 1024,
    "allowed_mime_types": VIDEO_MIMES,
}

# Supabase enforces a project-level max object size (50 MB on most plans)
# that overrides per-bucket file_size_limit. Annotated mp4s are usually
# smaller than the source anyway (we re-encode with libx264 veryfast at
# default crf), so 50 MB matches the upload cap and stays within the
# project ceiling.
ANNOTATED_OPTIONS = {
    "public": False,
    "file_size_limit": 50 * 1024 * 1024,
    "allowed_mime_types": VIDEO_MIMES,
}


def _ensure_bucket(client, bucket_id: str, options: dict, update_existing: bool) -> None:
    storage = client.storage
    try:
        existing = {b.id for b in storage.list_buckets()}
    except Exception as exc:
        print(f"❌ Could not list buckets ({exc}). Check SUPABASE_URL / service key.")
        raise SystemExit(1) from exc

    if bucket_id not in existing:
        try:
            storage.create_bucket(bucket_id, options=options)
            print(f"✅ Created bucket '{bucket_id}' (private, 50–60 MB cap).")
            return
        except Exception as exc:
            print(f"❌ Failed to create '{bucket_id}': {exc}")
            raise SystemExit(1) from exc

    if update_existing:
        try:
            storage.update_bucket(bucket_id, options)
            print(f"🔄 Updated bucket '{bucket_id}' options to match script.")
        except Exception as exc:
            print(f"❌ Failed to update '{bucket_id}': {exc}")
            raise SystemExit(1) from exc
    else:
        print(f"ℹ️  Bucket '{bucket_id}' already exists; leaving as-is. Pass --update to push options.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Push file_size_limit + allowed_mime_types onto existing buckets. "
            "Default behaviour leaves existing buckets untouched."
        ),
    )
    args = parser.parse_args()

    settings_url = os.environ.get("SUPABASE_URL", "")
    if not settings_url:
        print(
            f"❌ SUPABASE_URL is empty after loading {env_file}. "
            "Double-check the env file path."
        )
        return 1
    print(f"🚀 Stroke Lab bucket setup — target: {settings_url}")

    client = get_supabase_admin_client()
    _ensure_bucket(client, UPLOADS_BUCKET_ID, UPLOADS_OPTIONS, args.update)
    _ensure_bucket(client, ANNOTATED_BUCKET_ID, ANNOTATED_OPTIONS, args.update)
    print("✅ Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
