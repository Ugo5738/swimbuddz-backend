"""Public StrokeLab housekeeping: retry missed emails and enforce media retention.

Runs only on the isolated public AI worker, never inside API request handlers.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.config import AsyncSessionLocal
from services.ai_service.analysis.storage import delete_job_assets
from services.ai_service.models import (
    AnalysisJob,
    AnalysisJobSource,
    AnalysisJobStatus,
    AnalysisResult,
)
from services.ai_service.services.credit_ops import find_reservation
from services.ai_service.services.notify import send_failed_email, send_ready_email
from services.ai_service.tasks.analyze import _mark_email_sent, _mark_failed

logger = get_logger(__name__)


async def retry_unnotified_public_jobs(limit: int = 20) -> dict:
    """At-least-once recovery for successful/failed job emails.

    Delivery may be duplicated if the worker crashes between accepted delivery
    and recording email_sent_at. This is preferable to permanently lost notices.
    """
    cutoff = utc_now() - timedelta(minutes=10)
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AnalysisJob)
                    .where(
                        AnalysisJob.source == AnalysisJobSource.PUBLIC,
                        AnalysisJob.email_sent_at.is_(None),
                        # Also expire abandoned direct uploads and hung jobs.
                        AnalysisJob.status.in_(
                            [
                                AnalysisJobStatus.COMPLETED,
                                AnalysisJobStatus.FAILED,
                                AnalysisJobStatus.PENDING,
                                AnalysisJobStatus.PROCESSING,
                            ]
                        ),
                        AnalysisJob.completed_at <= cutoff,
                    )
                    .order_by(AnalysisJob.completed_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        snapshots = [
            (row.id, row.status, row.guest_email, row.guest_token, row.error_message)
            for row in rows
        ]

    sent = 0
    for job_id, status, email, token, reason in snapshots:
        if not email:
            continue
        try:
            if status == AnalysisJobStatus.COMPLETED and token:
                ok = await send_ready_email(job_id, email, token)
            elif status == AnalysisJobStatus.FAILED:
                ok = await send_failed_email(job_id, email, reason=reason)
            else:
                continue
            if ok:
                await _mark_email_sent(job_id)
                sent += 1
        except Exception:
            logger.exception("StrokeLab notification retry failed for job %s", job_id)
    return {"found": len(snapshots), "sent": sent}


async def fail_stale_public_jobs(limit: int = 100) -> dict:
    """Settle jobs abandoned by upload/queue/worker failures after six hours.

    The longest configured AI retry sequence should finish within this window.
    The existing failure path refunds only the job's live credit reservation.
    A delayed ARQ delivery cannot restart a failed job (worker terminal guard).
    """
    cutoff = utc_now() - timedelta(hours=6)
    async with AsyncSessionLocal() as session:
        ids = (
            (
                await session.execute(
                    select(AnalysisJob.id)
                    .where(
                        AnalysisJob.source == AnalysisJobSource.PUBLIC,
                        AnalysisJob.status.in_(
                            [AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING]
                        ),
                        AnalysisJob.created_at <= cutoff,
                    )
                    .order_by(AnalysisJob.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    failed = 0
    uploads_cleaned = 0
    for job_id in ids:
        try:
            # A direct upload initialized but never completed has no credit
            # reservation. Delete its S3 object promptly rather than paying to
            # store it for 30 days; keep submitted/retry footage for free retries.
            async with AsyncSessionLocal() as session:
                job = await session.get(AnalysisJob, job_id)
                if job is None or job.status not in (
                    AnalysisJobStatus.PENDING,
                    AnalysisJobStatus.PROCESSING,
                ):
                    continue
                if job.status == AnalysisJobStatus.PENDING:
                    reserved = await find_reservation(session, job_id=job_id)
                    if reserved is None and job.video_storage_path:
                        cleaned = await delete_job_assets(job.video_storage_path, None)
                        if cleaned:
                            job.video_storage_path = ""
                            await session.commit()
                            uploads_cleaned += 1
            await _mark_failed(job_id, "temporarily_unavailable")
            failed += 1
        except Exception:
            logger.exception("Could not settle abandoned StrokeLab job %s", job_id)
    return {"found": len(ids), "failed": failed, "uploads_cleaned": uploads_cleaned}


async def fail_stale_member_jobs(limit: int = 100) -> dict:
    """Expire abandoned member direct uploads and hung member analyses.

    Member work has no paid credit reservation; a failed job remains accessible
    to its owner for explicit erasure or another upload.
    """
    cutoff = utc_now() - timedelta(hours=6)
    async with AsyncSessionLocal() as session:
        ids = (
            await session.execute(
                select(AnalysisJob.id)
                .where(
                    AnalysisJob.source == AnalysisJobSource.MEMBER,
                    AnalysisJob.status.in_(
                        [AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING]
                    ),
                    AnalysisJob.created_at <= cutoff,
                )
                .order_by(AnalysisJob.created_at)
                .limit(limit)
            )
        ).scalars().all()

    failed = 0
    for job_id in ids:
        try:
            async with AsyncSessionLocal() as session:
                job = await session.get(AnalysisJob, job_id)
                if job is None or job.status not in (
                    AnalysisJobStatus.PENDING, AnalysisJobStatus.PROCESSING
                ):
                    continue
                # Never infer an unfinished session is a safe deletion target;
                # preserve the source video so the member can retry/review.
                job.status = AnalysisJobStatus.FAILED
                job.error_message = "temporarily_unavailable"
                job.completed_at = utc_now()
                await session.commit()
                failed += 1
        except Exception:
            logger.exception("Could not settle abandoned member StrokeLab job %s", job_id)
    return {"found": len(ids), "failed": failed}


async def purge_expired_public_jobs(limit: int = 100) -> dict:
    """Erase public analyses 30 days after creation, including generated images.

    Failed storage deletion preserves the DB row so the next pass can retry.
    Credit ledger history is independent of the job row and is kept for finance.
    """
    cutoff = utc_now() - timedelta(days=30)
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(AnalysisJob.id)
                    .where(
                        AnalysisJob.source == AnalysisJobSource.PUBLIC,
                        AnalysisJob.created_at <= cutoff,
                        AnalysisJob.status.in_(
                            [AnalysisJobStatus.COMPLETED, AnalysisJobStatus.FAILED]
                        ),
                    )
                    .order_by(AnalysisJob.created_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    purged = 0
    for job_id in rows:
        try:
            async with AsyncSessionLocal() as session:
                job = await session.get(AnalysisJob, job_id)
                if job is None:
                    continue
                if job.status in (
                    AnalysisJobStatus.PENDING,
                    AnalysisJobStatus.PROCESSING,
                ):
                    # Reuse the one-job credit refund path before erasing old uploads.
                    await session.rollback()
                    await _mark_failed(job_id, "temporarily_unavailable")
                    job = await session.get(AnalysisJob, job_id)
                    if job is None:
                        continue
                result = (
                    await session.execute(
                        select(AnalysisResult).where(AnalysisResult.job_id == job_id)
                    )
                ).scalar_one_or_none()
                payload = (result.coach_result or {}) if result is not None else {}
                evidence = list((payload.get("evidence_keys") or {}).values())
                evidence.extend((payload.get("share_keys") or {}).values())
                ok = await delete_job_assets(
                    job.video_storage_path, job.annotated_video_storage_path, evidence
                )
                if not ok:
                    logger.warning(
                        "StrokeLab retention deferred: storage cleanup failed for %s",
                        job_id,
                    )
                    continue
                await session.delete(job)
                await session.commit()
                purged += 1
        except Exception:
            logger.exception("StrokeLab retention failed for job %s", job_id)
    return {"found": len(rows), "purged": purged}
