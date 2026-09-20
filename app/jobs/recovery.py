"""
Job state recovery and startup reconciliation.
Recovers interrupted jobs and resets stale in-progress statuses.
"""

from __future__ import annotations

from pathlib import Path
from app.database.database import db
from app.database.models import EncodeJob
from app.jobs.job import JobStatus
from app.utils.logging import logger


class JobRecoveryManager:
    """Handles startup scan of active/interrupted jobs and cleans stale states."""

    @staticmethod
    async def recover_jobs() -> list[EncodeJob]:
        """
        Scans SQLite database for any jobs that were left in downloading, encoding, or uploading states.
        Resets them to 'queued' so the worker can resume them without losing already completed qualities.
        """
        unfinished = await db.get_unfinished_jobs()
        recovered: list[EncodeJob] = []

        for job in unfinished:
            if job.status in (JobStatus.DOWNLOADING, JobStatus.ENCODING, JobStatus.UPLOADING):
                logger.warning(f"Recovering interrupted job {job.job_id} (previous status: {job.status})")
                job.status = JobStatus.QUEUED
                job.current_quality = None
                job.progress_percent = 0.0
                job.progress_speed = "0x"
                job.progress_eta = "N/A"
                await db.save_job(job)
                recovered.append(job)
            elif job.status == JobStatus.QUEUED:
                recovered.append(job)

        if recovered:
            logger.info(f"Reconciled {len(recovered)} pending/interrupted jobs for queue processing.")
        return recovered
