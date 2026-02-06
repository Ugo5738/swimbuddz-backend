"""AI Service API routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.auth.dependencies import require_admin, require_service_role
from libs.auth.models import AuthUser
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AIModelConfig, AIPromptTemplate, AIRequest
from .schemas import (
    AIModelConfigCreate,
    AIModelConfigResponse,
    AIPromptTemplateCreate,
    AIPromptTemplateResponse,
    AIRequestListResponse,
    AIRequestResponse,
    CoachGradeScoringRequest,
    CoachGradeScoringResponse,
    CohortComplexityScoringRequest,
    CohortComplexityScoringResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["ai"])
admin_router = APIRouter(prefix="/admin", tags=["ai-admin"])


# ── Scoring Endpoints ──


@router.post("/score/cohort-complexity", response_model=CohortComplexityScoringResponse)
async def score_cohort_complexity_endpoint(
    request: CohortComplexityScoringRequest,
    model: Optional[str] = Query(None, description="Override the default model"),
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Score cohort complexity using AI.

    Requires service role authentication (internal service-to-service calls).
    Returns AI-suggested complexity scores across 7 dimensions.
    """
    from .scoring.cohort_complexity import score_cohort_complexity

    try:
        result, ai_response = await score_cohort_complexity(request, model=model)

        # Log the AI request
        ai_req = AIRequest(
            request_type="cohort_complexity",
            model_provider=ai_response.provider,
            model_name=ai_response.model,
            input_data=request.model_dump(),
            output_data=result.model_dump(),
            status="success",
            latency_ms=ai_response.latency_ms,
            input_tokens=ai_response.input_tokens,
            output_tokens=ai_response.output_tokens,
            cost_usd=ai_response.cost_usd,
            requesting_service="academy_service",
            langfuse_trace_id=ai_response.trace_id,
        )
        db.add(ai_req)
        await db.commit()
        await db.refresh(ai_req)

        result.ai_request_id = str(ai_req.id)
        return result

    except Exception as e:
        # Log failed request
        ai_req = AIRequest(
            request_type="cohort_complexity",
            model_provider="unknown",
            model_name=model or "default",
            input_data=request.model_dump(),
            status="error",
            error_message=str(e),
            requesting_service="academy_service",
        )
        db.add(ai_req)
        await db.commit()

        logger.error(f"Cohort complexity scoring failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"AI scoring failed: {str(e)}",
        )


@router.post("/score/coach-grade", response_model=CoachGradeScoringResponse)
async def score_coach_grade_endpoint(
    request: CoachGradeScoringRequest,
    model: Optional[str] = Query(None, description="Override the default model"),
    current_user: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Score coach grade progression using AI.

    Requires service role authentication (internal service-to-service calls).
    Returns AI-suggested grade with rationale.
    """
    from .scoring.coach_grade import score_coach_grade

    try:
        result, ai_response = await score_coach_grade(request, model=model)

        # Log the AI request
        ai_req = AIRequest(
            request_type="coach_grade",
            model_provider=ai_response.provider,
            model_name=ai_response.model,
            input_data=request.model_dump(),
            output_data=result.model_dump(),
            status="success",
            latency_ms=ai_response.latency_ms,
            input_tokens=ai_response.input_tokens,
            output_tokens=ai_response.output_tokens,
            cost_usd=ai_response.cost_usd,
            requesting_service="academy_service",
            langfuse_trace_id=ai_response.trace_id,
        )
        db.add(ai_req)
        await db.commit()
        await db.refresh(ai_req)

        result.ai_request_id = str(ai_req.id)
        return result

    except Exception as e:
        # Log failed request
        ai_req = AIRequest(
            request_type="coach_grade",
            model_provider="unknown",
            model_name=model or "default",
            input_data=request.model_dump(),
            status="error",
            error_message=str(e),
            requesting_service="academy_service",
        )
        db.add(ai_req)
        await db.commit()

        logger.error(f"Coach grade scoring failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"AI scoring failed: {str(e)}",
        )


# ── Admin Endpoints ──


@admin_router.get("/requests", response_model=AIRequestListResponse)
async def list_ai_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    request_type: Optional[str] = None,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List AI request logs (admin only)."""
    from sqlalchemy import func

    query = select(AIRequest).order_by(AIRequest.created_at.desc())
    count_query = select(func.count(AIRequest.id))

    if request_type:
        query = query.where(AIRequest.request_type == request_type)
        count_query = count_query.where(AIRequest.request_type == request_type)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = result.scalars().all()

    return AIRequestListResponse(
        items=[
            AIRequestResponse(
                id=str(r.id),
                request_type=r.request_type,
                model_provider=r.model_provider,
                model_name=r.model_name,
                input_data=r.input_data,
                output_data=r.output_data,
                status=r.status,
                error_message=r.error_message,
                latency_ms=r.latency_ms,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cost_usd=r.cost_usd,
                langfuse_trace_id=r.langfuse_trace_id,
                created_at=r.created_at,
            )
            for r in items
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@admin_router.get("/models", response_model=list[AIModelConfigResponse])
async def list_model_configs(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List configured AI models (admin only)."""
    result = await db.execute(
        select(AIModelConfig).order_by(AIModelConfig.created_at.desc())
    )
    configs = result.scalars().all()

    return [
        AIModelConfigResponse(
            id=str(c.id),
            provider=c.provider,
            model_name=c.model_name,
            is_enabled=c.is_enabled,
            is_default=c.is_default,
            max_tokens=c.max_tokens,
            temperature=c.temperature,
            input_cost_per_1k=c.input_cost_per_1k,
            output_cost_per_1k=c.output_cost_per_1k,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in configs
    ]


@admin_router.post("/models", response_model=AIModelConfigResponse)
async def create_model_config(
    data: AIModelConfigCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new AI model configuration (admin only)."""
    config = AIModelConfig(
        provider=data.provider,
        model_name=data.model_name,
        is_enabled=data.is_enabled,
        is_default=data.is_default,
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        input_cost_per_1k=data.input_cost_per_1k,
        output_cost_per_1k=data.output_cost_per_1k,
    )

    # If setting as default, unset other defaults
    if data.is_default:
        result = await db.execute(
            select(AIModelConfig).where(AIModelConfig.is_default.is_(True))
        )
        for existing in result.scalars().all():
            existing.is_default = False

    db.add(config)
    await db.commit()
    await db.refresh(config)

    return AIModelConfigResponse(
        id=str(config.id),
        provider=config.provider,
        model_name=config.model_name,
        is_enabled=config.is_enabled,
        is_default=config.is_default,
        max_tokens=config.max_tokens,
        temperature=config.temperature,
        input_cost_per_1k=config.input_cost_per_1k,
        output_cost_per_1k=config.output_cost_per_1k,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@admin_router.get("/prompts", response_model=list[AIPromptTemplateResponse])
async def list_prompt_templates(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List prompt templates (admin only)."""
    result = await db.execute(
        select(AIPromptTemplate).order_by(
            AIPromptTemplate.name, AIPromptTemplate.version.desc()
        )
    )
    templates = result.scalars().all()

    return [
        AIPromptTemplateResponse(
            id=str(t.id),
            name=t.name,
            version=t.version,
            is_active=t.is_active,
            system_prompt=t.system_prompt,
            user_prompt_template=t.user_prompt_template,
            output_schema=t.output_schema,
            created_at=t.created_at,
        )
        for t in templates
    ]


@admin_router.post("/prompts", response_model=AIPromptTemplateResponse)
async def create_prompt_template(
    data: AIPromptTemplateCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new prompt template version (admin only)."""
    from sqlalchemy import func

    # Get next version number for this prompt name
    version_result = await db.execute(
        select(func.max(AIPromptTemplate.version)).where(
            AIPromptTemplate.name == data.name
        )
    )
    current_max = version_result.scalar() or 0
    next_version = current_max + 1

    # Deactivate previous versions
    result = await db.execute(
        select(AIPromptTemplate).where(
            AIPromptTemplate.name == data.name,
            AIPromptTemplate.is_active.is_(True),
        )
    )
    for old in result.scalars().all():
        old.is_active = False

    template = AIPromptTemplate(
        name=data.name,
        version=next_version,
        is_active=True,
        system_prompt=data.system_prompt,
        user_prompt_template=data.user_prompt_template,
        output_schema=data.output_schema,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    return AIPromptTemplateResponse(
        id=str(template.id),
        name=template.name,
        version=template.version,
        is_active=template.is_active,
        system_prompt=template.system_prompt,
        user_prompt_template=template.user_prompt_template,
        output_schema=template.output_schema,
        created_at=template.created_at,
    )
