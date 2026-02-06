"""Seed initial AI model configurations into the database.

Creates default model configs for OpenAI and Anthropic providers.
"""

import asyncio
import os
import sys
from uuid import uuid4

# Add backend root to path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from libs.db.config import AsyncSessionLocal
from services.ai_service.models import AIModelConfig
from sqlalchemy.future import select

SEED_MODELS = [
    {
        "provider": "openai",
        "model_name": "gpt-4o-mini",
        "is_enabled": True,
        "is_default": True,
        "max_tokens": 4096,
        "temperature": 0.1,
        "input_cost_per_1k": 0.00015,
        "output_cost_per_1k": 0.0006,
    },
    {
        "provider": "openai",
        "model_name": "gpt-4o",
        "is_enabled": True,
        "is_default": False,
        "max_tokens": 4096,
        "temperature": 0.1,
        "input_cost_per_1k": 0.0025,
        "output_cost_per_1k": 0.01,
    },
    {
        "provider": "anthropic",
        "model_name": "claude-3-5-sonnet-20241022",
        "is_enabled": True,
        "is_default": False,
        "max_tokens": 4096,
        "temperature": 0.1,
        "input_cost_per_1k": 0.003,
        "output_cost_per_1k": 0.015,
    },
]


async def seed_ai_config():
    """Seed initial AI model configurations."""
    async with AsyncSessionLocal() as session:
        for model_data in SEED_MODELS:
            # Check if already exists
            result = await session.execute(
                select(AIModelConfig).where(
                    AIModelConfig.provider == model_data["provider"],
                    AIModelConfig.model_name == model_data["model_name"],
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                print(
                    f"  AI model config {model_data['provider']}/{model_data['model_name']} already exists, skipping"
                )
                continue

            config = AIModelConfig(id=uuid4(), **model_data)
            session.add(config)
            print(
                f"  Seeded AI model config: {model_data['provider']}/{model_data['model_name']}"
            )

        await session.commit()


if __name__ == "__main__":
    asyncio.run(seed_ai_config())
