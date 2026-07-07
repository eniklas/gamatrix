"""Enrichment jobs are split into fixed-size chunks, one SQS message each, so no
single enricher invocation has to enrich the whole library within the Lambda
15-min timeout. These tests cover the fan-out on creation and the per-chunk
processing / completion / progress accounting on the enricher side.
"""

from __future__ import annotations

import gamatrix.igdb.enricher as enricher
import gamatrix.jobs as jobs
from gamatrix.constants import ENRICHMENT_DONE, JOB_COMPLETED, JOB_RUNNING
from gamatrix.igdb.enricher import run_job
from gamatrix.jobs import create_enrichment_job


class _RecordingQueue:
    """Captures enqueue(job_id, chunk_index) calls instead of hitting SQS."""

    def __init__(self):
        self.messages: list[tuple[str, int]] = []

    def enqueue(self, job_id: str, chunk_index: int) -> None:
        self.messages.append((job_id, chunk_index))


def _seed_done_games(repo, release_keys):
    # Games already enriched: the enricher does no IGDB work for them (so these
    # tests need no network), but they must still count toward progress.
    for rk in release_keys:
        repo.put_game(
            {
                "release_key": rk,
                "title": rk,
                "igdb_key": rk,
                "platform": rk.split("_")[0],
                "enrichment_status": ENRICHMENT_DONE,
            }
        )


def test_create_enrichment_job_fans_out_one_message_per_chunk(repo, monkeypatch):
    monkeypatch.setattr(jobs, "ENRICHMENT_CHUNK_SIZE", 2)
    queue = _RecordingQueue()
    keys = ["steam_1", "steam_2", "steam_3", "steam_4", "steam_5"]

    job_id = create_enrichment_job(repo, queue, keys)

    # 5 keys / chunk size 2 -> chunks 0, 1, 2.
    assert [idx for _, idx in queue.messages] == [0, 1, 2]
    assert all(jid == job_id for jid, _ in queue.messages)
    job = repo.get_job(job_id)
    assert job["total"] == 5
    assert job["release_keys"] == keys


def test_create_enrichment_job_dedupes_before_chunking(repo, monkeypatch):
    monkeypatch.setattr(jobs, "ENRICHMENT_CHUNK_SIZE", 2)
    queue = _RecordingQueue()

    job_id = create_enrichment_job(repo, queue, ["steam_1", "steam_1", "steam_2"])

    assert repo.get_job(job_id)["release_keys"] == ["steam_1", "steam_2"]
    assert [idx for _, idx in queue.messages] == [0]


async def test_chunk_completes_job_only_when_all_chunks_done(
    repo, settings, monkeypatch
):
    monkeypatch.setattr(enricher, "ENRICHMENT_CHUNK_SIZE", 2)
    monkeypatch.setattr(jobs, "ENRICHMENT_CHUNK_SIZE", 2)
    keys = ["steam_1", "steam_2", "steam_3", "steam_4", "steam_5"]
    _seed_done_games(repo, keys)
    job_id = create_enrichment_job(repo, _RecordingQueue(), keys)

    await run_job(job_id, repo, settings=settings, chunk_index=0)
    job = repo.get_job(job_id)
    assert job["status"] == JOB_RUNNING
    assert job["completed_count"] == 2

    await run_job(job_id, repo, settings=settings, chunk_index=1)
    assert repo.get_job(job_id)["status"] == JOB_RUNNING

    await run_job(job_id, repo, settings=settings, chunk_index=2)
    job = repo.get_job(job_id)
    assert job["status"] == JOB_COMPLETED
    assert job["completed_count"] == 5
    assert job["completed_at"]


async def test_redelivered_chunk_does_not_inflate_progress(repo, settings, monkeypatch):
    monkeypatch.setattr(enricher, "ENRICHMENT_CHUNK_SIZE", 2)
    monkeypatch.setattr(jobs, "ENRICHMENT_CHUNK_SIZE", 2)
    keys = ["steam_1", "steam_2", "steam_3"]
    _seed_done_games(repo, keys)
    job_id = create_enrichment_job(repo, _RecordingQueue(), keys)

    await run_job(job_id, repo, settings=settings, chunk_index=0)
    # SQS redelivers the same chunk: its slot is set absolutely, so the total
    # can't climb past `total` (see #131).
    await run_job(job_id, repo, settings=settings, chunk_index=0)
    await run_job(job_id, repo, settings=settings, chunk_index=1)

    job = repo.get_job(job_id)
    assert job["completed_count"] == 3
    assert job["status"] == JOB_COMPLETED


async def test_local_worker_path_runs_whole_job_in_one_pass(
    repo, settings, monkeypatch
):
    # chunk_index=None is how the local worker invokes run_job: no SQS fan-out,
    # so one call must process every key and complete the job.
    monkeypatch.setattr(enricher, "ENRICHMENT_CHUNK_SIZE", 2)
    monkeypatch.setattr(jobs, "ENRICHMENT_CHUNK_SIZE", 2)
    keys = ["steam_1", "steam_2", "steam_3", "steam_4", "steam_5"]
    _seed_done_games(repo, keys)
    job_id = create_enrichment_job(repo, _RecordingQueue(), keys)

    await run_job(job_id, repo, settings=settings)

    job = repo.get_job(job_id)
    assert job["status"] == JOB_COMPLETED
    assert job["completed_count"] == 5
