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


async def task_process_recurring_payouts(ctx: dict):
    from services.payments_service.tasks import process_recurring_payouts

    logger.info("Running: process_recurring_payouts")
    await process_recurring_payouts()


async def task_expire_overdue_makeups(ctx: dict):
    from services.payments_service.tasks import expire_overdue_makeups

    logger.info("Running: expire_overdue_makeups")
    await expire_overdue_makeups()


class WorkerSettings:
    redis_settings = get_redis_settings()
    queue_name = "arq:payments"

    functions = [
        task_reconcile_pending_payments,
        task_retry_payment_fulfillment,
        task_process_recurring_payouts,
        task_expire_overdue_makeups,
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
        # Recurring coach payouts: daily at 02:15 UTC (~03:15 WAT). Picks up
        # any active configs whose next_run_date has arrived and creates a
        # PENDING CoachPayout for admin approval.
        cron(
            task_process_recurring_payouts,
            hour={2},
            minute={15},
            run_at_startup=False,
        ),
        # Expire stale make-up obligations: daily at 02:30 UTC. Anything past
        # cohort end_date that wasn't completed becomes EXPIRED.
        cron(
            task_expire_overdue_makeups,
            hour={2},
            minute={30},
            run_at_startup=False,
        ),
    ]
