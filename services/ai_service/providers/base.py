"""Base AI provider interface.

All providers (OpenAI, Anthropic, etc.) implement this interface
via LiteLLM for model-agnostic routing.
"""

import json
import time
from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


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
