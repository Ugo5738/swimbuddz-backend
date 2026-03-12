"""
Background task for generating featured images for content posts using DALL-E.

Reads image prompts from the seed data JSON, generates images via LiteLLM,
uploads them to the media service, and links them to their ContentPost.
"""

import json
from pathlib import Path

import httpx
import litellm
from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select

from services.communications_service.models import ContentPost

logger = get_logger(__name__)

SEED_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed-data" / "content_posts.json"
)


def _load_image_prompts() -> dict[str, str]:
    """Load title → featured_image_prompt mapping from seed JSON."""
    if not SEED_PATH.exists():
        logger.warning("Seed data not found at %s", SEED_PATH)
        return {}

    with open(SEED_PATH, "r", encoding="utf-8") as f:
        posts = json.load(f)

    return {
        p["title"]: p["featured_image_prompt"]
        for p in posts
        if p.get("featured_image_prompt")
    }


async def _generate_and_upload_image(prompt: str, title: str) -> str | None:
    """Generate image with DALL-E and upload to media service. Returns media_id or None."""
    settings = get_settings()

    # 1. Generate image via LiteLLM (DALL-E)
    try:
        response = await litellm.aimage_generation(
            model="dall-e-3",
            prompt=prompt,
            size="1792x1024",
            quality="standard",
            n=1,
        )
        image_url = response.data[0].url
    except Exception:
        logger.error("DALL-E generation failed for '%s'", title, exc_info=True)
        return None

    # 2. Download the generated image (DALL-E URLs are temporary)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            img_response = await client.get(image_url)
            img_response.raise_for_status()
            image_bytes = img_response.content
    except Exception:
        logger.error(
            "Failed to download generated image for '%s'", title, exc_info=True
        )
        return None

    # 3. Upload to media service
    try:
        media_url = settings.MEDIA_SERVICE_URL
        token = _service_role_jwt("communications")

        async with httpx.AsyncClient(timeout=30.0) as client:
            upload_response = await client.post(
                f"{media_url}/api/v1/media/uploads",
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": (
                        f"content-{title[:30].replace(' ', '-')}.png",
                        image_bytes,
                        "image/png",
                    )
                },
                data={"purpose": "content_image", "title": f"Featured: {title[:50]}"},
            )
            upload_response.raise_for_status()
            media_data = upload_response.json()
            return media_data["id"]
    except Exception:
        logger.error(
            "Failed to upload image to media service for '%s'", title, exc_info=True
        )
        return None


async def generate_content_images() -> None:
    """
    Find content posts without featured images and generate them using DALL-E.
    Prompts are read from the seed data JSON file.
    """
    prompts_map = _load_image_prompts()
    if not prompts_map:
        logger.info("No image prompts found in seed data.")
        return

    async for db in get_async_db():
        try:
            # Find posts without featured images
            query = select(ContentPost).where(
                ContentPost.featured_image_media_id.is_(None),
            )
            result = await db.execute(query)
            posts = result.scalars().all()

            if not posts:
                logger.info("All content posts already have featured images.")
                return

            generated = 0
            skipped = 0

            for post in posts:
                prompt = prompts_map.get(post.title)
                if not prompt:
                    skipped += 1
                    continue

                logger.info("Generating image for: %s", post.title)
                media_id = await _generate_and_upload_image(prompt, post.title)

                if media_id:
                    post.featured_image_media_id = media_id
                    generated += 1
                    logger.info("Image generated and linked for: %s", post.title)
                else:
                    skipped += 1
                    logger.warning(
                        "Image generation failed for: %s (will retry next run)",
                        post.title,
                    )

            if generated > 0:
                await db.commit()

            logger.info(
                "Content image generation complete: %d generated, %d skipped",
                generated,
                skipped,
            )

        except Exception:
            logger.error("Error in content image generation task", exc_info=True)
            await db.rollback()
        finally:
            await db.close()
            break
