"""Ingest a parsed GOG Galaxy DB into DynamoDB and trigger enrichment.

Used by both the S3-triggered db_parser Lambda (AWS) and the upload-complete
endpoint (local dev). Writes the user's library, upserts game stubs, and
creates an enrichment job for any games not yet enriched.
"""

from __future__ import annotations

import logging

from gamatrix.constants import ENRICHMENT_PENDING
from gamatrix.gogdb.parser import GogDBParser, ParsedLibrary
from gamatrix.helpers import now_iso
from gamatrix.jobs import create_enrichment_job
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import EnrichmentQueue

log = logging.getLogger(__name__)


def _retained_entries(
    repo: Repository, parsed: ParsedLibrary, entries: list[dict]
) -> list[dict]:
    """Stored rows this upload must not delete (issue #186).

    ``replace_user_library`` makes the library exactly what it is given, so a
    title missing from the upload is dropped. That is right for a refund, a
    cancelled subscription, or a title leaving Game Pass — the platform is still
    connected and simply stopped reporting the game. It is wrong when the user
    disconnected the integration, because GOG then purges every one of its
    titles and we would wipe a platform the user still owns games on.

    So deletions are scoped to the platforms the parser vouched for. Rows on any
    other platform are carried over untouched, which keeps their original
    ``db_updated_at`` as a record of when they were last actually seen.
    """
    incoming_keys = {entry["release_key"] for entry in entries}
    retained = [
        row
        for row in repo.get_user_library(parsed.user_id)
        if row["release_key"] not in incoming_keys
        and row.get("platform", row["release_key"].split("_")[0])
        not in parsed.authoritative_platforms
    ]
    if retained:
        log.info(
            "Keeping %d stored titles for user %s from platforms absent from "
            "this DB",
            len(retained),
            parsed.user_id,
        )
    return retained


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
    retained = _retained_entries(repo, parsed, entries)
    repo.replace_user_library(parsed.user_id, entries + retained)

    user = repo.get_user_by_user_id(parsed.user_id)
    if user:
        repo.update_user(user["email"], {"db_updated_at": timestamp})

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
        "Ingested user %s: %d library entries (%d retained), %d new games to enrich",
        parsed.user_id,
        len(entries),
        len(retained),
        len(to_enrich),
    )
    return parsed.user_id, job_id
