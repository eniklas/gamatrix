"""Tests for enrichment job progress and staleness handling.

Covers the two failure modes behind the stuck progress bar and #131's
inaccurate counts: a job left non-terminal must stop driving the UI, and
progress must stay idempotent across SQS redeliveries / concurrent runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gamatrix.constants import (
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    JOB_TIMEOUT_MINUTES,
)
from gamatrix.helpers import now_iso
from gamatrix.jobs import is_job_active, is_job_stale


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _job(repo, **overrides):
    job = {
        "job_id": overrides.get("job_id", "j1"),
        "status": JOB_RUNNING,
        "created_at": now_iso(),
        "completed_at": None,
        "release_keys": ["steam_1", "steam_2"],
        "total": 2,
        "completed_count": 0,
    }
    job.update(overrides)
    repo.put_job(job)
    return job


def test_is_job_active():
    assert is_job_active({"status": JOB_PENDING})
    assert is_job_active({"status": JOB_RUNNING})
    assert not is_job_active({"status": JOB_COMPLETED})
    assert not is_job_active({"status": "failed"})


def test_fresh_running_job_is_not_stale():
    assert not is_job_stale({"status": JOB_RUNNING, "updated_at": now_iso()})


def test_old_running_job_is_stale():
    job = {"status": JOB_RUNNING, "updated_at": _ago(JOB_TIMEOUT_MINUTES + 1)}
    assert is_job_stale(job)


def test_terminal_job_is_never_stale():
    # Completed jobs aren't "active", so staleness doesn't apply even if old.
    assert not is_job_stale({"status": JOB_COMPLETED, "updated_at": _ago(999)})


def test_staleness_measured_from_progress_not_creation():
    # A long-running job that created hours ago but is still making progress
    # must not be reaped.
    job = {
        "status": JOB_RUNNING,
        "created_at": _ago(120),
        "updated_at": _ago(1),
    }
    assert not is_job_stale(job)


def test_staleness_falls_back_to_created_at():
    # A job that started running but never recorded progress.
    job = {"status": JOB_RUNNING, "created_at": _ago(JOB_TIMEOUT_MINUTES + 1)}
    assert is_job_stale(job)


def test_get_active_job_skips_stale(repo):
    _job(repo, job_id="dead", updated_at=_ago(JOB_TIMEOUT_MINUTES + 5))
    assert repo.get_active_job() is None


def test_get_active_job_returns_fresh(repo):
    _job(repo, job_id="live", updated_at=now_iso())
    active = repo.get_active_job()
    assert active is not None
    assert active["job_id"] == "live"


def test_get_active_job_prefers_fresh_over_stale(repo):
    _job(repo, job_id="dead", created_at=_ago(60), updated_at=_ago(60))
    _job(repo, job_id="live", created_at=now_iso(), updated_at=now_iso())
    active = repo.get_active_job()
    assert active["job_id"] == "live"


def test_fail_stale_jobs_reaps_only_stale_active(repo):
    _job(repo, job_id="dead", updated_at=_ago(JOB_TIMEOUT_MINUTES + 5))
    _job(repo, job_id="live", updated_at=now_iso())
    _job(repo, job_id="done", status=JOB_COMPLETED, updated_at=_ago(999))

    reaped = repo.fail_stale_jobs()

    assert reaped == ["dead"]
    assert repo.get_job("dead")["status"] == JOB_FAILED
    assert repo.get_job("dead")["completed_at"]
    assert repo.get_job("live")["status"] == JOB_RUNNING
    assert repo.get_job("done")["status"] == JOB_COMPLETED


def test_get_active_job_self_heals_stale(repo):
    # A presumed-dead job is marked failed in passing, not just skipped, so the
    # job record reflects reality and stops blocking new enrichment.
    _job(repo, job_id="dead", updated_at=_ago(JOB_TIMEOUT_MINUTES + 5))

    assert repo.get_active_job() is None
    assert repo.get_job("dead")["status"] == JOB_FAILED


def test_set_job_progress_is_absolute_and_idempotent(repo):
    _job(repo, job_id="j1", completed_count=0)
    repo.set_job_progress("j1", 1)
    repo.set_job_progress("j1", 2)
    # A redelivered run replays the same values; the count must not climb past
    # what it sets, unlike an atomic increment (see #131).
    repo.set_job_progress("j1", 1)
    repo.set_job_progress("j1", 2)
    job = repo.get_job("j1")
    assert job["completed_count"] == 2
    assert job["updated_at"]
