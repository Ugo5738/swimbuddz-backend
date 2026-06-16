"""ARQ worker config for the AI service.

Run with:

    arq services.ai_service.tasks.worker.WorkerSettings

The worker shares its image with the FastAPI service today; v1 may split
them so the API container doesn't ship the torch/ultralytics binaries.
"""

from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger
from services.ai_service.constants import MEMBER_QUEUE_NAME, PUBLIC_QUEUE_NAME

logger = get_logger(__name__)


async def task_analyze_swim_video(ctx: dict, job_id: str) -> dict:
    """ARQ wrapper. Delegates to services.ai_service.tasks.analyze."""
    from services.ai_service.tasks.analyze import analyze_swim_video

    logger.info("Running: task_analyze_swim_video for %s", job_id)
    return await analyze_swim_video(job_id)


class WorkerSettings:
    """ARQ worker settings (member queue)."""

    redis_settings = get_redis_settings()
    queue_name = MEMBER_QUEUE_NAME

    # YOLO + pose inference is CPU-bound; one job per worker process keeps
    # context switching down. Scale by adding more worker containers, not
    # by increasing max_jobs.
    max_jobs = 1

    # One attempt only. task_analyze_swim_video catches its own exceptions and
    # returns a status dict (never raises), so arq never retries — but pin it so
    # a future change can't silently enable arq's default retries and re-run a
    # public job whose credit was already consumed/refunded (design §6.5).
    max_tries = 1

    # Generous timeout — design budget is <90s per minute of video, but
    # cold-starts have to download the pose + YOLO models (~10 MB) and a
    # 50 MB upload before any inference starts.
    job_timeout = 600

    functions = [task_analyze_swim_video]
    cron_jobs = []


class PublicWorkerSettings:
    """Isolated worker for PUBLIC (guest analyzer) jobs.

    Same pipeline + limits as the member worker, but bound to the public queue
    and run as a SEPARATE, CPU/mem-capped container (docker-compose
    ``ai-worker-public``) so a Reddit traffic spike can't starve member
    analyses on ``arq:ai``. Run with:

        arq services.ai_service.tasks.worker.PublicWorkerSettings

    NOTE: arq reads settings from the class's OWN ``__dict__``, not inherited
    attributes — so this must NOT subclass WorkerSettings (a subclass would
    expose only its overridden ``queue_name`` and arq would then see no
    ``functions`` → "at least one function or cron_job must be registered" and
    crash-loop). Every attribute is declared here, referencing WorkerSettings'
    values to avoid drift.
    """

    redis_settings = WorkerSettings.redis_settings
    queue_name = PUBLIC_QUEUE_NAME
    max_jobs = WorkerSettings.max_jobs
    max_tries = WorkerSettings.max_tries
    job_timeout = WorkerSettings.job_timeout
    functions = WorkerSettings.functions
    cron_jobs = WorkerSettings.cron_jobs
