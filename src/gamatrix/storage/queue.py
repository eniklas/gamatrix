"""Enrichment job queue abstraction.

In AWS, enqueueing a job publishes its id to SQS, which triggers the enricher
Lambda. Locally, no queue is configured (enrichment_queue_url is unset); the
job row in DynamoDB is the source of truth and the local_worker polls for it.
"""

from __future__ import annotations

import json
import logging

import boto3

from gamatrix.config import Settings, get_settings

log = logging.getLogger(__name__)


class EnrichmentQueue:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None
        if self.settings.enrichment_queue_url:
            self._client = boto3.client(
                "sqs",
                region_name=self.settings.aws_region,
                endpoint_url=self.settings.sqs_endpoint_url,
            )

    def enqueue(self, job_id: str) -> None:
        """Publish a job id. No-op when no queue is configured (local dev)."""
        if self._client is None or self.settings.enrichment_queue_url is None:
            log.info("No SQS queue configured; job %s left for local worker", job_id)
            return
        self._client.send_message(
            QueueUrl=self.settings.enrichment_queue_url,
            MessageBody=json.dumps({"job_id": job_id}),
        )


_queue: EnrichmentQueue | None = None


def get_queue() -> EnrichmentQueue:
    global _queue
    if _queue is None:
        _queue = EnrichmentQueue()
    return _queue
