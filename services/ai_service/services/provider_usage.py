"""Provider usage ledger helpers for Stroke Lab.

Gemini does not provide a stable per-response "remaining quota" counter. We log
our own VLM calls to ``ai_requests`` and estimate remaining capacity against
operator-configured ceilings (GEMINI_*_LIMIT). The estimate is honest: it covers
traffic that went through this backend/key, not Google-side usage outside it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from services.ai_service.models import AIRequest


def summarize_events(events: list[dict]) -> dict[str, Any]:
    successes = [e for e in events if e.get("kind") == "success"]
    retries = [e for e in events if e.get("kind") == "retry"]
    failures = [e for e in events if e.get("kind") == "failure"]
    models = sorted({str(e.get("model")) for e in successes if e.get("model")})
    providers = sorted({str(e.get("provider")) for e in successes if e.get("provider")})
    return {
        "calls": len(successes),
        "input_tokens": sum(int(e.get("input_tokens") or 0) for e in successes),
        "output_tokens": sum(int(e.get("output_tokens") or 0) for e in successes),
        "total_tokens": sum(int(e.get("total_tokens") or 0) for e in successes),
        "cost_usd": round(sum(float(e.get("cost_usd") or 0.0) for e in successes), 6),
        "models": models,
        "providers": providers,
        "retry_count": len(retries),
        "failure_count": len(failures),
        "retry_reasons": sorted(
            {str(e.get("reason")) for e in retries if e.get("reason")}
        ),
        "last_retry": retries[-1] if retries else None,
        "last_failure": failures[-1] if failures else None,
        "events": events[-20:],
    }


async def record_usage_events(
    session: AsyncSession,
    *,
    events: list[dict],
    request_type: str,
    requesting_service: str = "ai_service",
) -> dict[str, Any]:
    """Persist provider events to ai_requests and return a run summary."""
    summary = summarize_events(events)
    for event in events:
        if event.get("kind") not in {"success", "failure"}:
            continue
        session.add(
            AIRequest(
                request_type=request_type,
                model_provider=str(event.get("provider") or "unknown"),
                model_name=str(event.get("model") or "unknown"),
                input_data={
                    "trace_name": event.get("trace_name"),
                    "event_kind": event.get("kind"),
                    "reason": event.get("reason"),
                },
                output_data=event,
                status="success" if event.get("kind") == "success" else "error",
                error_message=event.get("message")
                if event.get("kind") == "failure"
                else None,
                latency_ms=int(event.get("latency_ms") or 0) or None,
                input_tokens=int(event.get("input_tokens") or 0) or None,
                output_tokens=int(event.get("output_tokens") or 0) or None,
                cost_usd=float(event.get("cost_usd") or 0.0) or None,
                requesting_service=requesting_service,
            )
        )
    return summary


async def gemini_quota_estimate(session: AsyncSession) -> dict[str, Any]:
    """Estimate remaining Gemini quota from the local ai_requests ledger."""
    settings = get_settings()
    now = utc_now()
    minute_start = now - timedelta(minutes=1)
    day_start = now - timedelta(days=1)

    base = [
        AIRequest.model_provider.in_(["google", "gemini"]),
        AIRequest.model_name.ilike("%gemini%"),
        AIRequest.status == "success",
    ]

    minute = await session.execute(
        select(
            func.count(AIRequest.id),
            func.coalesce(func.sum(AIRequest.input_tokens), 0),
            func.coalesce(func.sum(AIRequest.output_tokens), 0),
        ).where(*base, AIRequest.created_at >= minute_start)
    )
    minute_calls, minute_in, minute_out = minute.one()

    day = await session.execute(
        select(func.count(AIRequest.id)).where(*base, AIRequest.created_at >= day_start)
    )
    day_calls = int(day.scalar() or 0)

    minute_tokens = int(minute_in or 0) + int(minute_out or 0)
    return {
        "source": "app_ledger_estimate",
        "note": "Estimated from SwimBuddz ai_requests only; Google does not return exact remaining quota per response.",
        "rpm_limit": settings.GEMINI_RPM_LIMIT,
        "tpm_limit": settings.GEMINI_TPM_LIMIT,
        "rpd_limit": settings.GEMINI_RPD_LIMIT,
        "rpm_used": int(minute_calls or 0),
        "tpm_used": minute_tokens,
        "rpd_used": day_calls,
        "rpm_remaining": max(0, settings.GEMINI_RPM_LIMIT - int(minute_calls or 0))
        if settings.GEMINI_RPM_LIMIT
        else None,
        "tpm_remaining": max(0, settings.GEMINI_TPM_LIMIT - minute_tokens)
        if settings.GEMINI_TPM_LIMIT
        else None,
        "rpd_remaining": max(0, settings.GEMINI_RPD_LIMIT - day_calls)
        if settings.GEMINI_RPD_LIMIT
        else None,
        "window_utc": {
            "minute_start": minute_start.isoformat(),
            "day_start": day_start.isoformat(),
        },
    }
