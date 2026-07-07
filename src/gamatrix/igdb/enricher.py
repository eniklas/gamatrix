"""Run an enrichment job: look up IGDB metadata and write it to DynamoDB.

Invoked by the SQS-triggered enricher Lambda in AWS and by the local_worker in
development. A job's release keys are split into fixed-size chunks, one SQS
message each, so no single invocation has to enrich the whole library within the
Lambda 15-min timeout. The Lambda passes the message's `chunk_index`; the local
worker passes none and runs the whole job in one pass.
"""

from __future__ import annotations

import logging

from gamatrix.config import Settings, get_settings, resolve_igdb_credentials
from gamatrix.constants import (
    ENRICHMENT_CHUNK_SIZE,
    ENRICHMENT_DONE,
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
    IGDB_API_CALL_DELAY,
    JOB_COMPLETED,
    JOB_RUNNING,
)
from gamatrix.helpers import now_iso
from gamatrix.igdb.client import GameMetadata, IGDBClient
from gamatrix.storage.dynamo import Repository

log = logging.getLogger(__name__)


async def run_job(
    job_id: str,
    repo: Repository,
    settings: Settings | None = None,
    chunk_index: int | None = None,
) -> None:
    settings = settings or get_settings()
    job = repo.get_job(job_id)
    if job is None:
        log.error("Enrichment job %s not found", job_id)
        return

    repo.update_job(job_id, {"status": JOB_RUNNING, "updated_at": now_iso()})
    release_keys: list[str] = job.get("release_keys", [])
    total: int = job.get("total", len(release_keys))

    # A chunk_index slices this invocation's share of the job; the local worker
    # passes none and takes the whole job (there is no Lambda timeout locally).
    if chunk_index is None:
        keys, chunk_id = release_keys, "all"
    else:
        start = chunk_index * ENRICHMENT_CHUNK_SIZE
        keys = release_keys[start : start + ENRICHMENT_CHUNK_SIZE]
        chunk_id = str(chunk_index)

    # Group release keys by the IGDB key so games shared across platforms
    # (e.g. a Steam and a GOG copy) only cost one set of API calls.
    games = repo.batch_get_games(keys)
    by_igdb_key: dict[str, list[str]] = {}
    for rk in keys:
        game = games.get(rk)
        if game is None:
            continue
        if game.get("enrichment_status") not in (None, ENRICHMENT_PENDING):
            continue
        by_igdb_key.setdefault(game["igdb_key"], []).append(rk)

    # Keys needing no work (missing from the table, or already enriched by an
    # earlier delivery) still count as processed so the bar can reach 100%.
    to_enrich = sum(len(rks) for rks in by_igdb_key.values())
    completed = len(keys) - to_enrich
    progress = repo.set_chunk_progress(job_id, chunk_id, completed)

    if by_igdb_key:
        client_id, client_secret = resolve_igdb_credentials(settings)
        # Parallel enricher invocations each pace themselves, so scale the delay
        # by the concurrency: N workers at (N x base) delay share the 4 req/sec
        # budget instead of each consuming all of it and tripping IGDB's throttle.
        call_delay = IGDB_API_CALL_DELAY * max(settings.enricher_max_concurrency, 1)
        async with IGDBClient(client_id, client_secret, call_delay) as client:
            for igdb_key, rks in by_igdb_key.items():
                # Use any sharing release key's title for matching.
                title = games[rks[0]].get("title", "")
                try:
                    meta = await client.fetch_metadata(igdb_key, title)
                except Exception:  # one game's failure shouldn't sink the chunk
                    log.exception("Failed to enrich %s (%s)", igdb_key, title)
                    meta = GameMetadata()
                for rk in rks:
                    _write_metadata(repo, games[rk], meta)
                    completed += 1
                # Absolute per-chunk progress: a redelivered or concurrent run
                # of this chunk converges here instead of pushing the count past
                # `total` (see #131).
                progress = repo.set_chunk_progress(job_id, chunk_id, completed)

    # The chunk that accounts for the final outstanding keys closes the job.
    # Idempotent: re-completing an already-completed job is harmless.
    if sum(progress.values()) >= total:
        repo.update_job(job_id, {"status": JOB_COMPLETED, "completed_at": now_iso()})
        log.info("Enrichment job %s completed (%d games)", job_id, total)
    else:
        log.info(
            "Enrichment job %s chunk %s done (%d keys)", job_id, chunk_id, len(keys)
        )


def _write_metadata(repo: Repository, game: dict, meta: GameMetadata) -> None:
    status = ENRICHMENT_DONE if meta.found else ENRICHMENT_NOT_FOUND
    repo.put_game(
        {
            **game,
            "igdb_id": meta.igdb_id,
            "game_modes": meta.game_modes,
            "max_players": meta.max_players,
            "multiplayer": meta.multiplayer,
            "rating": meta.rating,
            "rating_count": meta.rating_count,
            "enrichment_status": status,
            "enriched_at": now_iso(),
        }
    )
