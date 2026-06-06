"""SQS-triggered Lambda: run IGDB enrichment jobs.

Each SQS message carries a job_id produced by gamatrix.jobs.create_enrichment_job.
"""

from __future__ import annotations

import asyncio
import json
import logging

from gamatrix.igdb.enricher import run_job
from gamatrix.storage.dynamo import get_repository

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def handler(event, context):
    repo = get_repository()
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        job_id = body["job_id"]
        log.info("Processing enrichment job %s", job_id)
        asyncio.run(run_job(job_id, repo))
    return {"statusCode": 200}
