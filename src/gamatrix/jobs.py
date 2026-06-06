"""Enrichment job creation, shared by DB ingest and the web routes."""

from __future__ import annotations

import logging
import uuid

from gamatrix.constants import JOB_PENDING
from gamatrix.helpers import now_iso
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import EnrichmentQueue

log = logging.getLogger(__name__)


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
