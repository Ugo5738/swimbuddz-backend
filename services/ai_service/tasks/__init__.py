"""AI service background tasks (ARQ).

Exposes one task today: ``analyze_swim_video`` for Stroke Lab.

Run the worker container with:

    arq services.ai_service.tasks.worker.WorkerSettings
"""

from services.ai_service.tasks.analyze import analyze_swim_video

__all__ = ["analyze_swim_video"]
