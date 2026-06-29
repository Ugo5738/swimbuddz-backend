"""Base AI provider interface.

All providers (OpenAI, Anthropic, etc.) implement this interface
via LiteLLM for model-agnostic routing.
"""

import asyncio
import contextvars
import json
import re
import time
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

_VLM_EVENTS: contextvars.ContextVar[Optional[list[dict]]] = contextvars.ContextVar(
    "vlm_events", default=None
)


def start_vlm_usage_capture():
    """Start collecting VLM call telemetry in the current async context."""
    return _VLM_EVENTS.set([])


def stop_vlm_usage_capture(token) -> list[dict]:
    """Stop collecting VLM telemetry and return the captured events."""
    events = list(_VLM_EVENTS.get() or [])
    _VLM_EVENTS.reset(token)
    return events


def _record_vlm_event(event: dict) -> None:
    bucket = _VLM_EVENTS.get()
    if bucket is not None:
        bucket.append(event)


class AIProviderResponse:
    """Standardized response from any AI provider."""

    def __init__(
        self,
        content: str,
        model: str,
        provider: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        cost_usd: float = 0.0,
        raw_response: Optional[dict] = None,
        trace_id: Optional[str] = None,
    ):
        self.content = content
        self.model = model
        self.provider = provider
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd
        self.raw_response = raw_response
        self.trace_id = trace_id

    def parse_json(self) -> dict:
        """Parse the content as JSON. Handles markdown code blocks."""
        text = self.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        return json.loads(text)


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    response_format: Optional[dict] = None,
    trace_name: Optional[str] = None,
) -> AIProviderResponse:
    """
    Call an LLM via LiteLLM with optional Langfuse tracing.

    Uses LiteLLM for model-agnostic routing so the same code works
    with OpenAI, Anthropic, Google, or any supported provider.

    Args:
        system_prompt: System message
        user_prompt: User message
        model: LiteLLM model string (e.g., "gpt-4o", "claude-3-5-sonnet-20241022")
        temperature: Sampling temperature
        max_tokens: Max output tokens
        response_format: Optional JSON schema for structured output
        trace_name: Name for Langfuse trace (if enabled)
    """
    import litellm

    settings = get_settings()

    # Default model
    if not model:
        model = getattr(settings, "AI_DEFAULT_MODEL", "gpt-4o-mini")

    # Determine provider from model string
    provider = "unknown"
    if "gpt" in model or "o1" in model or "o3" in model:
        provider = "openai"
    elif "claude" in model:
        provider = "anthropic"
    elif "gemini" in model:
        provider = "google"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Build kwargs
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    # Langfuse callback if available
    langfuse_trace_id = None
    try:
        langfuse_url = getattr(settings, "LANGFUSE_HOST", "")
        if langfuse_url:
            kwargs["metadata"] = {
                "trace_name": trace_name or "swimbuddz_ai",
            }
            litellm.success_callback = ["langfuse"]
            litellm.failure_callback = ["langfuse"]
    except Exception:
        pass  # Langfuse not configured, continue without

    start = time.monotonic()
    try:
        response = await litellm.acompletion(**kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        content = response.choices[0].message.content or ""
        usage = response.usage

        return AIProviderResponse(
            content=content,
            model=model,
            provider=provider,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=elapsed_ms,
            cost_usd=0.0,  # Cost computed separately if needed
            raw_response=(
                response.model_dump() if hasattr(response, "model_dump") else None
            ),
            trace_id=langfuse_trace_id,
        )
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            f"LLM call failed: {e}", extra={"model": model, "latency_ms": elapsed_ms}
        )
        raise


def _provider_from_model(model: str) -> str:
    """Best-effort provider label from a LiteLLM model string."""
    if "gpt" in model or "o1" in model or "o3" in model:
        return "openai"
    if "claude" in model:
        return "anthropic"
    if "gemini" in model:
        return "google"
    if "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def _retry_after_seconds(msg: str) -> Optional[float]:
    """Best-effort parse of a provider's requested retry delay from an error.

    Gemini 429s embed ``"retryDelay": "26s"``; OpenAI/HTTP use ``Retry-After``.
    Returns the delay in seconds (+1s buffer, capped) or None if not present.
    """
    m = re.search(
        r'ret(?:ry)?[\-_ ]?(?:after|delay)["\s:]*?(\d+(?:\.\d+)?)\s*s?', msg, re.I
    )
    if not m:
        return None
    try:
        return min(90.0, float(m.group(1)) + 1.0)
    except ValueError:
        return None


async def call_vlm(
    system_prompt: str,
    user_prompt: str,
    images: list,
    model: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    image_detail: str = "auto",
    response_format: Optional[dict] = None,
    num_retries: int = 4,
    trace_name: Optional[str] = None,
    video: Optional[bytes] = None,
    video_mime: str = "video/mp4",
) -> AIProviderResponse:
    """Vision (multimodal) sibling of :func:`call_llm`.

    ``images`` are raw JPEG/PNG bytes. They are base64-encoded into OpenAI-style
    ``image_url`` content blocks, which LiteLLM routes to whatever provider the
    ``model`` string selects — OpenAI, Anthropic, Gemini, or a self-hosted
    open-weights endpoint (``ollama/...``, ``openrouter/...``). The engine stays
    provider-agnostic: only the model string changes when we move from a hosted
    Tier-A model to an open-source Tier-B/C one. ``cost_usd`` is populated from
    LiteLLM's own pricing tables so callers get a real per-call cost.
    """
    import base64

    import litellm

    # Drop provider-unsupported params instead of erroring — keeps the layer
    # agnostic (e.g. OpenAI o-series reasoning models reject temperature != 1;
    # LiteLLM will silently drop temperature for them rather than 400).
    litellm.drop_params = True

    settings = get_settings()
    if not model:
        model = getattr(settings, "AI_VISION_MODEL", "") or getattr(
            settings, "AI_DEFAULT_MODEL", "gpt-4o-mini"
        )
    provider = _provider_from_model(model)

    content: list[dict] = [{"type": "text", "text": user_prompt}]
    for img in images:
        b64 = base64.b64encode(img).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                    "detail": image_detail,
                },
            }
        )
    # Video input (Gemini): a base64 data-URI "file" block. LiteLLM maps file_data
    # to Gemini's inline_data (mime + bytes). Inline only — the caller size-guards;
    # larger clips fall back to stills (a Gemini File-API upload is the follow-up).
    if video is not None:
        vb64 = base64.b64encode(video).decode("ascii")
        content.append(
            {
                "type": "file",
                "file": {"file_data": f"data:{video_mime};base64,{vb64}"},
            }
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        # LiteLLM retries RateLimitError/timeout with exponential backoff and
        # honours Retry-After — essential on low TPM caps (OpenAI gpt-4o = 30k).
        "num_retries": num_retries,
    }
    # Pass the provider key explicitly (overrides env) so a swap is just config —
    # no need to also juggle which *_API_KEY env var LiteLLM picks up.
    _key = {"google": settings.GEMINI_API_KEY, "openai": settings.OPENAI_API_KEY}.get(
        provider
    )
    if _key:
        kwargs["api_key"] = _key
    if response_format:
        kwargs["response_format"] = response_format

    start = time.monotonic()
    # Retry transient AND rate-limit errors ourselves. LiteLLM's num_retries gives up
    # within ~tens of seconds, but two free-tier failure modes need more patience:
    #   • 503 "high demand"/overloaded — a momentary capacity blip (short backoff).
    #   • 429 "exceeded your current quota" — a PER-MINUTE window; LiteLLM bails too
    #     fast, so we wait toward ~60s (honouring the provider's retryDelay) for the
    #     window to reset. This is an async job — the user is emailed when it's done,
    #     so a patient wait is far better than failing the analysis. Without this,
    #     running several coaches on Gemini's free tier reliably 429s the later calls.
    MAX_ATTEMPTS = 6
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await litellm.acompletion(**kwargs)
            break
        except Exception as e:
            name, msg = type(e).__name__, str(e)
            low = msg.lower()
            rate_limited = (
                "ratelimit" in name.lower()
                or "429" in msg
                or "quota" in low
                or "resource_exhausted" in low
                or "rate limit" in low
            )
            transient = (
                "ServiceUnavailable" in name
                or "InternalServerError" in name
                or "Overloaded" in name
                or "503" in msg
                or "UNAVAILABLE" in msg
                or "overloaded" in low
            )
            if (rate_limited or transient) and attempt < MAX_ATTEMPTS - 1:
                if rate_limited:
                    delay = _retry_after_seconds(msg) or min(
                        60.0, 20.0 + 15.0 * attempt
                    )
                else:
                    delay = 1.5 * (2**attempt)
                logger.warning(
                    "VLM %s — retry %d/%d in %.0fs (%s) model=%s",
                    "rate-limited" if rate_limited else "transient",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    delay,
                    name,
                    model,
                )
                _record_vlm_event(
                    {
                        "kind": "retry",
                        "reason": "rate_limited" if rate_limited else "transient",
                        "attempt": attempt + 1,
                        "max_attempts": MAX_ATTEMPTS,
                        "delay_s": delay,
                        "error_type": name,
                        "message": msg[:500],
                        "model": model,
                        "provider": provider,
                        "trace_name": trace_name,
                    }
                )
                await asyncio.sleep(delay)
                continue
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                f"VLM call failed: {e}",
                extra={"model": model, "latency_ms": elapsed_ms},
            )
            _record_vlm_event(
                {
                    "kind": "failure",
                    "reason": "rate_limited"
                    if rate_limited
                    else "transient"
                    if transient
                    else "error",
                    "attempt": attempt + 1,
                    "max_attempts": MAX_ATTEMPTS,
                    "error_type": name,
                    "message": msg[:500],
                    "model": model,
                    "provider": provider,
                    "trace_name": trace_name,
                    "latency_ms": elapsed_ms,
                }
            )
            raise

    elapsed_ms = int((time.monotonic() - start) * 1000)
    text = response.choices[0].message.content or ""
    usage = response.usage
    try:
        cost = float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        cost = 0.0

    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    _record_vlm_event(
        {
            "kind": "success",
            "model": model,
            "provider": provider,
            "trace_name": trace_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost_usd": cost,
            "latency_ms": elapsed_ms,
        }
    )

    return AIProviderResponse(
        content=text,
        model=model,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=elapsed_ms,
        cost_usd=cost,
        raw_response=(
            response.model_dump() if hasattr(response, "model_dump") else None
        ),
    )
