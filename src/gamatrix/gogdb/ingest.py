"""Ingest a parsed GOG Galaxy DB into DynamoDB and trigger enrichment.

Used by both the S3-triggered db_parser Lambda (AWS) and the upload-complete
endpoint (local dev). Writes the user's library, upserts game stubs, and
creates an enrichment job for any games not yet enriched.
"""

from __future__ import annotations

import logging

from gamatrix.constants import ENRICHMENT_PENDING
from gamatrix.gogdb.parser import GogDBParser
from gamatrix.helpers import now_iso
from gamatrix.jobs import create_enrichment_job
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import EnrichmentQueue

log = logging.getLogger(__name__)


def ingest_db_file(
    db_path: str,
    repo: Repository,
    queue: EnrichmentQueue,
) -> tuple[str, str | None]:
    """Parse a DB file and persist it. Returns (user_id, enrichment_job_id)."""
    parser = GogDBParser(db_path)
    try:
        parsed = parser.parse()
    finally:
        parser.close()

    timestamp = now_iso()
    entries = [{**e, "db_updated_at": timestamp} for e in parsed.entries]
    repo.replace_user_library(parsed.user_id, entries)

    # Upsert game stubs; collect release keys that still need IGDB enrichment.
    to_enrich: list[str] = []
    for stub in parsed.games:
        existing = repo.get_game(stub["release_key"])
        if existing is None:
            repo.put_game(
                {
                    **stub,
                    "enrichment_status": ENRICHMENT_PENDING,
                    "max_players": 0,
                    "multiplayer": False,
                    "rating": 0,
                    "enriched_at": None,
                }
            )
            to_enrich.append(stub["release_key"])
        else:
            # Keep IGDB fields; refresh the GOG-derived ones in case of changes.
            repo.put_game(
                {
                    **existing,
                    "title": stub["title"],
                    "slug": stub["slug"],
                    "igdb_key": stub["igdb_key"],
                    "platform": stub["platform"],
                }
            )
            if existing.get("enrichment_status") == ENRICHMENT_PENDING:
                to_enrich.append(stub["release_key"])

    job_id = create_enrichment_job(repo, queue, to_enrich)
    log.info(
        "Ingested user %s: %d library entries, %d new games to enrich",
        parsed.user_id,
        len(entries),
        len(to_enrich),
    )
    return parsed.user_id, job_id
