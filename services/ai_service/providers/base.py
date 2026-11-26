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


def _enforce_strokelab_vlm_budget(model: str) -> None:
    """Bound the number of model calls and spend inside one StrokeLab attempt.

    The events list is shared by concurrent gate votes. The check and append
    happen synchronously before the first await so every model call reserves
    its slot. Cost is checked against actual successful provider charges;
    a single final call can exceed the ceiling by its own cost.
    """
    events = _VLM_EVENTS.get()
    if events is None:
        return  # Other AI-service workloads are not subject to StrokeLab caps.
    settings = get_settings()
    attempted = sum(1 for event in events if event.get("kind") == "attempt")
    spent = sum(
        float(event.get("cost_usd") or 0)
        for event in events
        if event.get("kind") == "success"
    )
    if (
        attempted >= settings.STROKELAB_MAX_MODEL_CALLS_PER_ATTEMPT
        or spent >= settings.STROKELAB_MAX_MODEL_COST_PER_ATTEMPT_USD
    ):
        _record_vlm_event(
            {
                "kind": "budget_exhausted",
                "model": model,
                "attempts": attempted,
                "cost_usd": spent,
            }
        )
        raise RuntimeError("StrokeLab AI budget exhausted for this attempt")
    _record_vlm_event({"kind": "attempt", "model": model})


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
        """Parse the content as JSON.

        Handles markdown code fences and recovers cleanly when thinking models
        emit short prose before or after a single top-level JSON object.
        """
        text = self.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Extract the first balanced JSON object when prose wraps valid JSON.
            start = text.find("{")
            if start != -1:
                decoder = json.JSONDecoder()
                obj, _ = decoder.raw_decode(text[start:])
                if isinstance(obj, dict):
                    return obj
            raise


class AIImageProviderResponse:
    """Standardized response from an image-generation provider."""

    def __init__(
        self,
        *,
        image_url: str,
        model: str,
        provider: str,
        latency_ms: int = 0,
        cost_usd: float = 0.0,
        raw_response: Optional[dict] = None,
    ):
        self.image_url = image_url
        self.model = model
        self.provider = provider
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd
        self.raw_response = raw_response


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
    if response_format is not None:
        kwargs["response_format"] = response_format

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


async def call_image_generation(
    *,
    prompt: str,
    model: Optional[str] = None,
    size: str = "1792x1024",
    quality: str = "standard",
    trace_name: Optional[str] = None,
) -> AIImageProviderResponse:
    """Generate one image through the AI service's provider boundary."""
    import litellm

    settings = get_settings()
    model = model or getattr(settings, "AI_IMAGE_MODEL", "dall-e-3")
    provider = _provider_from_model(model)
    kwargs = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
    }

    try:
        langfuse_url = getattr(settings, "LANGFUSE_HOST", "")
        if langfuse_url:
            kwargs["metadata"] = {"trace_name": trace_name or "swimbuddz_image"}
            litellm.success_callback = ["langfuse"]
            litellm.failure_callback = ["langfuse"]
    except Exception:
        pass

    start = time.monotonic()
    try:
        response = await litellm.aimage_generation(**kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        data = response.get("data") if isinstance(response, dict) else response.data
        if not data:
            raise RuntimeError("Image provider returned no image data")
        first = data[0]
        image_url = first.get("url") if isinstance(first, dict) else first.url
        if not image_url:
            raise RuntimeError("Image provider returned no image URL")
        return AIImageProviderResponse(
            image_url=image_url,
            model=model,
            provider=provider,
            latency_ms=elapsed_ms,
            raw_response=(
                response.model_dump() if hasattr(response, "model_dump") else None
            ),
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "Image generation failed: %s",
            exc,
            extra={"model": model, "latency_ms": elapsed_ms},
        )
        raise


# Conservative fallback token rates (USD per 1M input / output tokens) used ONLY
# when LiteLLM's bundled pricing table does not recognize a newer model ID and
# returns 0.0. Anchored on standard post-promo (Jan 2027) list rates so cost
# circuit breakers and spend dashboards never go blind on newly released models.
_FALLBACK_MODEL_RATES_USD_PER_1M: tuple[tuple[str, tuple[float, float]], ...] = (
    ("gemini-3.8-flash", (1.50, 7.50)),
    ("gemini-3.6-flash", (1.50, 7.50)),
    ("gemini-3.5-flash-lite", (0.30, 2.50)),
    ("gemini-3.5-flash", (1.50, 9.00)),
    ("gemini-3.1-flash-lite", (0.30, 2.50)),
    ("gemini-3-flash", (0.75, 3.75)),
    ("gemini-3-pro", (2.50, 15.00)),
    ("gemini-2.5-flash-lite", (0.15, 0.60)),
    ("gemini-2.5-flash", (0.30, 2.50)),
    ("gpt-5.6", (4.00, 20.00)),
    ("gpt-5.4-nano", (0.20, 1.25)),
    ("gpt-5.4-mini", (0.75, 4.50)),
    ("gpt-5.4", (2.50, 15.00)),
    ("gpt-5-nano", (0.15, 0.60)),
    ("gpt-5-mini", (0.50, 2.00)),
    ("o4-mini", (1.10, 4.40)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.00)),
    ("qwen3-vl", (0.40, 1.20)),
    ("qwen2.5-vl", (0.30, 0.90)),
)
_DEFAULT_UNKNOWN_RATES_USD_PER_1M = (1.50, 7.50)

_VALID_VIDEO_DETAILS = {"low", "medium", "high", "ultra_high"}
_GEMINI_THINKING_MIN_JSON_MAX_TOKENS = 4096


def estimate_model_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from conservative standard token rates.

    Used as a safety net when LiteLLM lacks a pricing entry for a newer model
    so per-attempt and daily AI cost circuit breakers remain active.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return 0.0
    low = (model or "").lower()
    in_rate, out_rate = _DEFAULT_UNKNOWN_RATES_USD_PER_1M
    for needle, rates in _FALLBACK_MODEL_RATES_USD_PER_1M:
        if needle in low:
            in_rate, out_rate = rates
            break
    return round(
        (max(0, input_tokens) * in_rate + max(0, output_tokens) * out_rate)
        / 1_000_000.0,
        8,
    )


def _is_gemini_3_or_newer(model: str) -> bool:
    return "gemini-3" in (model or "").lower()


def _is_gemini_thinking_model(model: str) -> bool:
    low = (model or "").lower()
    return "gemini-3" in low or "gemini-2.5" in low


def _provider_from_model(model: str) -> str:
    """Best-effort provider label from a LiteLLM model string."""
    low = (model or "").lower()
    if any(tok in low for tok in ("gpt", "o1", "o3", "o4", "dall-e")):
        return "openai"
    if "claude" in low:
        return "anthropic"
    if "gemini" in low:
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
    video_fps: Optional[float] = None,
    video_detail: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
) -> AIProviderResponse:
    """Vision (multimodal) sibling of :func:`call_llm`.

    ``images`` are raw JPEG/PNG bytes. They are base64-encoded into OpenAI-style
    ``image_url`` content blocks, which LiteLLM routes to whatever provider the
    ``model`` string selects — OpenAI, Anthropic, Gemini, or a self-hosted
    open-weights endpoint (``ollama/...``, ``openrouter/...``). The engine stays
    provider-agnostic: only the model string changes when we move from a hosted
    Tier-A model to an open-source Tier-B/C one. ``cost_usd`` is populated from
    LiteLLM's pricing tables with a conservative fallback rate table for newer
    models so cost circuit breakers never go blind.
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
    _enforce_strokelab_vlm_budget(model)

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
    # to Gemini's inline_data (mime + bytes), and on Gemini 3+ forwards
    # video_metadata.fps + per-part media_resolution (detail) so short stroke
    # chunks are sampled densely instead of defaulting to 1 FPS.
    if video is not None:
        vb64 = base64.b64encode(video).decode("ascii")
        file_payload: dict = {
            "file_data": f"data:{video_mime};base64,{vb64}",
            "format": video_mime,
        }
        if _is_gemini_3_or_newer(model):
            if video_fps is not None and video_fps > 0:
                file_payload["video_metadata"] = {"fps": float(video_fps)}
            if video_detail and video_detail in _VALID_VIDEO_DETAILS:
                file_payload["detail"] = video_detail
        content.append({"type": "file", "file": file_payload})

    effective_max_tokens = max_tokens
    # Gemini 2.5 / 3.x thinking tokens count against maxOutputTokens; ensure
    # structured JSON calls always have enough headroom to avoid truncated JSON.
    if response_format and _is_gemini_thinking_model(model):
        effective_max_tokens = max(
            effective_max_tokens, _GEMINI_THINKING_MIN_JSON_MAX_TOKENS
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": effective_max_tokens,
        # LiteLLM retries RateLimitError/timeout with exponential backoff and
        # honours Retry-After — essential on low TPM caps (OpenAI gpt-4o = 30k).
        "num_retries": num_retries,
    }
    if reasoning_effort and _is_gemini_thinking_model(model):
        kwargs["reasoning_effort"] = reasoning_effort
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
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    try:
        cost = float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        cost = 0.0
    cost_estimated = False
    if cost <= 0.0 and (input_tokens > 0 or output_tokens > 0):
        cost = estimate_model_cost_usd(model, input_tokens, output_tokens)
        cost_estimated = cost > 0.0

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
            "cost_estimated": cost_estimated,
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
