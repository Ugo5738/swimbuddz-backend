"""Internal generative endpoints for SwimBuddz editorial content."""

from fastapi import APIRouter, Depends, HTTPException
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai_service.context import SWIMBUDDZ_CONTENT_CONTEXT_VERSION
from services.ai_service.generation.content import (
    ContentGenerationError,
    generate_article_draft,
)
from services.ai_service.models import AIRequest
from services.ai_service.providers.base import call_image_generation
from services.ai_service.schemas.content import (
    ContentDraftRequest,
    ContentDraftResponse,
    ContentImageRequest,
    ContentImageResponse,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/content", tags=["ai-content"])


def _requesting_service(user: AuthUser) -> str:
    return user.user_id.removeprefix("service:")


@router.post("/drafts", response_model=ContentDraftResponse)
async def create_content_draft(
    request: ContentDraftRequest,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> ContentDraftResponse:
    model: str | None = None
    try:
        payload, ai_response = await generate_article_draft(request, model=model)
        output = payload.model_dump()
        ai_request = AIRequest(
            request_type="content_article_draft",
            model_provider=ai_response.provider,
            model_name=ai_response.model,
            input_data={
                **request.model_dump(),
                "context_version": SWIMBUDDZ_CONTENT_CONTEXT_VERSION,
            },
            output_data=output,
            status="success",
            latency_ms=ai_response.latency_ms,
            input_tokens=ai_response.input_tokens,
            output_tokens=ai_response.output_tokens,
            cost_usd=ai_response.cost_usd,
            requesting_service=_requesting_service(current_user),
            langfuse_trace_id=ai_response.trace_id,
        )
        db.add(ai_request)
        await db.commit()
        await db.refresh(ai_request)
        return ContentDraftResponse(
            **output,
            ai_request_id=str(ai_request.id),
            model_used=ai_response.model,
            context_version=SWIMBUDDZ_CONTENT_CONTEXT_VERSION,
        )
    except ContentGenerationError as exc:
        error = exc
    except Exception as exc:
        logger.exception("Article draft generation failed")
        error = exc

    await db.rollback()
    ai_request = AIRequest(
        request_type="content_article_draft",
        model_provider="unknown",
        model_name=model or "default",
        input_data={
            **request.model_dump(),
            "context_version": SWIMBUDDZ_CONTENT_CONTEXT_VERSION,
        },
        status="error",
        error_message=str(error),
        requesting_service=_requesting_service(current_user),
    )
    db.add(ai_request)
    await db.commit()
    raise HTTPException(status_code=503, detail="AI draft generation is unavailable")


@router.post("/images", response_model=ContentImageResponse)
async def create_content_image(
    request: ContentImageRequest,
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
) -> ContentImageResponse:
    try:
        provider_response = await call_image_generation(
            prompt=request.prompt,
            trace_name="content_featured_image",
        )
        ai_request = AIRequest(
            request_type="content_featured_image",
            model_provider=provider_response.provider,
            model_name=provider_response.model,
            input_data=request.model_dump(),
            output_data={"image_url": provider_response.image_url},
            status="success",
            latency_ms=provider_response.latency_ms,
            cost_usd=provider_response.cost_usd,
            requesting_service=_requesting_service(current_user),
        )
        db.add(ai_request)
        await db.commit()
        await db.refresh(ai_request)
        return ContentImageResponse(
            image_url=provider_response.image_url,
            ai_request_id=str(ai_request.id),
            model_used=provider_response.model,
        )
    except Exception as exc:
        logger.exception("Content image generation failed")
        await db.rollback()
        ai_request = AIRequest(
            request_type="content_featured_image",
            model_provider="unknown",
            model_name="default",
            input_data=request.model_dump(),
            status="error",
            error_message=str(exc),
            requesting_service=_requesting_service(current_user),
        )
        db.add(ai_request)
        await db.commit()
        raise HTTPException(
            status_code=503, detail="AI image generation is unavailable"
        )
