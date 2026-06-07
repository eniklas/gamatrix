"""Enrichment job creation, shared by DB ingest and the web routes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from gamatrix.constants import (
    JOB_PENDING,
    JOB_RUNNING,
    JOB_TIMEOUT_MINUTES,
)
from gamatrix.helpers import now_iso, parse_iso
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import EnrichmentQueue

log = logging.getLogger(__name__)


def is_job_active(job: dict) -> bool:
    """True while a job is still pending or running (not yet terminal)."""
    return job.get("status") in (JOB_PENDING, JOB_RUNNING)


def is_job_stale(job: dict) -> bool:
    """True for an active job that has made no progress within the timeout.

    Such a job is presumed dead — the enricher Lambda crashed or hit its hard
    timeout without writing a terminal status — so the UI should treat it as
    finished rather than poll it forever. Staleness is measured from the last
    progress (`updated_at`), falling back to `created_at` for a job that never
    progressed."""
    if not is_job_active(job):
        return False
    last_seen = job.get("updated_at") or job.get("created_at")
    if not last_seen:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=JOB_TIMEOUT_MINUTES)
    return parse_iso(last_seen) < cutoff


def create_enrichment_job(
    repo: Repository,
    queue: EnrichmentQueue,
    release_keys: list[str],
) -> str | None:
    """Create a pending enrichment job and enqueue it. Returns the job id, or
    None when there is nothing to enrich."""
    release_keys = list(dict.fromkeys(release_keys))
    if not release_keys:
        return None

    job_id = str(uuid.uuid4())
    repo.put_job(
        {
            "job_id": job_id,
            "status": JOB_PENDING,
            "created_at": now_iso(),
            "completed_at": None,
            "release_keys": release_keys,
            "total": len(release_keys),
            "completed_count": 0,
        }
    )
    queue.enqueue(job_id)
    log.info("Created enrichment job %s for %d games", job_id, len(release_keys))
    return job_id
