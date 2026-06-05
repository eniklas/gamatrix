"""S3-triggered Lambda: parse an uploaded GOG Galaxy DB into DynamoDB.

Fires when a file lands in the upload bucket. Downloads it, ingests the user's
library, and enqueues an enrichment job for any new games.
"""

from __future__ import annotations

import logging
import os
import tempfile
import urllib.parse

from gamatrix.gogdb.ingest import ingest_db_file
from gamatrix.storage.dynamo import get_repository
from gamatrix.storage.queue import get_queue
from gamatrix.storage.s3 import get_s3

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def handler(event, context):
    repo = get_repository()
    queue = get_queue()
    s3 = get_s3()

    for record in event.get("Records", []):
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        log.info("Parsing uploaded DB %s", key)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            s3.download(key, path)
            user_id, job_id = ingest_db_file(path, repo, queue)
            log.info("Ingested user %s (job %s)", user_id, job_id)
            # Link the account to its GOG user id if the upload key encodes the email.
            if key.startswith("uploads/") and key.endswith(".db"):
                email = key[len("uploads/") : -len(".db")]
                user = repo.get_user(email)
                if user and str(user.get("user_id") or "") != user_id:
                    repo.update_user(email, {"user_id": user_id})
        finally:
            os.remove(path)
            s3.delete(key)  # don't retain user DBs

    return {"statusCode": 200}
