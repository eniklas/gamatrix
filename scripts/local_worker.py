#!/usr/bin/env python3
"""Local stand-in for the SQS-triggered enricher Lambda.

There is no SQS locally, so this polls the enrichment_jobs table for pending
jobs and runs them, exactly as the Lambda would on an SQS message.

    just worker      # or: python scripts/local_worker.py
"""

from __future__ import annotations

import asyncio
import logging
import time

from gamatrix.igdb.enricher import run_job
from gamatrix.storage.dynamo import get_repository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("local_worker")

POLL_SECONDS = 5


def main() -> None:
    repo = get_repository()
    log.info("Local enrichment worker started; polling every %ss", POLL_SECONDS)
    while True:
        try:
            pending = repo.list_pending_jobs()
        except Exception:
            log.exception("Failed to list pending jobs")
            pending = []
        for job in pending:
            log.info("Running job %s (%d games)", job["job_id"], job.get("total", 0))
            try:
                # No chunk_index: locally there's no SQS fan-out, so the worker
                # runs the whole job in one pass.
                asyncio.run(run_job(job["job_id"], repo))
            except Exception:
                # One bad job shouldn't kill the poll loop; the stale reaper will
                # mark it dead so it stops blocking new enrichment.
                log.exception("Enrichment job %s failed", job["job_id"])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
