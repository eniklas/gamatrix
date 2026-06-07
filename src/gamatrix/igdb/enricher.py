"""Run an enrichment job: look up IGDB metadata and write it to DynamoDB.

Invoked by the SQS-triggered enricher Lambda in AWS and by the local_worker in
development. Both call run_job(job_id) after constructing a Repository.
"""

from __future__ import annotations

import logging

from gamatrix.config import Settings, get_settings, resolve_igdb_credentials
from gamatrix.constants import (
    ENRICHMENT_DONE,
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_RUNNING,
)
from gamatrix.helpers import now_iso
from gamatrix.igdb.client import GameMetadata, IGDBClient
from gamatrix.storage.dynamo import Repository

log = logging.getLogger(__name__)


async def run_job(
    job_id: str, repo: Repository, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    job = repo.get_job(job_id)
    if job is None:
        log.error("Enrichment job %s not found", job_id)
        return

    repo.update_job(job_id, {"status": JOB_RUNNING})
    release_keys: list[str] = job["release_keys"]

    # Group release keys by the IGDB key so games shared across platforms
    # (e.g. a Steam and a GOG copy) only cost one set of API calls.
    games = repo.batch_get_games(release_keys)
    by_igdb_key: dict[str, list[str]] = {}
    for rk in release_keys:
        game = games.get(rk)
        if game is None:
            continue
        if game.get("enrichment_status") not in (None, ENRICHMENT_PENDING):
            continue
        by_igdb_key.setdefault(game["igdb_key"], []).append(rk)

    client_id, client_secret = resolve_igdb_credentials(settings)
    try:
        async with IGDBClient(client_id, client_secret) as client:
            for igdb_key, rks in by_igdb_key.items():
                # Use any sharing release key's title for matching.
                title = games[rks[0]].get("title", "")
                try:
                    meta = await client.fetch_metadata(igdb_key, title)
                except Exception:  # one game's failure shouldn't sink the job
                    log.exception("Failed to enrich %s (%s)", igdb_key, title)
                    meta = GameMetadata()
                for rk in rks:
                    _write_metadata(repo, games[rk], meta)
                    repo.increment_job_progress(job_id, 1)
    except Exception:
        log.exception("Enrichment job %s failed", job_id)
        repo.update_job(job_id, {"status": JOB_FAILED, "completed_at": now_iso()})
        return

    repo.update_job(job_id, {"status": JOB_COMPLETED, "completed_at": now_iso()})
    log.info("Enrichment job %s completed (%d games)", job_id, len(release_keys))


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
