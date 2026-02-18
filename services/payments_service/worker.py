"""ARQ worker for payments reconciliation and fulfillment retries."""

from arq import cron
from libs.common.arq_config import get_redis_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


async def task_reconcile_pending_payments(ctx: dict):
    from services.payments_service.tasks import reconcile_pending_paystack_payments

    logger.info("Running: reconcile_pending_paystack_payments")
    await reconcile_pending_paystack_payments()


async def task_retry_payment_fulfillment(ctx: dict):
    from services.payments_service.tasks import retry_failed_entitlement_fulfillment

    logger.info("Running: retry_failed_entitlement_fulfillment")
    await retry_failed_entitlement_fulfillment()


class WorkerSettings:
    redis_settings = get_redis_settings()

    functions = [
        task_reconcile_pending_payments,
        task_retry_payment_fulfillment,
    ]

    cron_jobs = [
        cron(
            task_reconcile_pending_payments,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
        cron(
            task_retry_payment_fulfillment,
            minute={1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56},
            run_at_startup=True,
        ),
    ]
