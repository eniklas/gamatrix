"""SQS-triggered Lambda: run one chunk of an IGDB enrichment job.

Each SQS message carries a `job_id` and `chunk_index` produced by
gamatrix.jobs.create_enrichment_job. The chunk_index selects this invocation's
slice of the job's release keys so no single run has to enrich the whole library
within the Lambda timeout.
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
        chunk_index = body.get("chunk_index")
        log.info("Processing enrichment job %s chunk %s", job_id, chunk_index)
        asyncio.run(run_job(job_id, repo, chunk_index=chunk_index))
    return {"statusCode": 200}
