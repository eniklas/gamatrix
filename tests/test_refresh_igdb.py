"""Regression tests for the IGDB refresh routes.

`Refresh missing IGDB` re-enriches games IGDB didn't find last time. The
enricher only processes games whose status is unset or `pending` (#134), so the
route must flip the targeted `not_found` games to `pending` first — otherwise
every game it queues is silently skipped and the button does nothing.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from gamatrix.app import app
from gamatrix.auth.dependencies import get_repo, require_admin
from gamatrix.constants import (
    ENRICHMENT_DONE,
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
)
from gamatrix.helpers import now_iso


def _client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[require_admin] = lambda: {
        "email": "admin@example.com",
        "is_admin": True,
    }
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _game(repo, rk, status):
    repo.put_game(
        {
            "release_key": rk,
            "title": rk,
            "igdb_key": rk,
            "platform": rk.split("_")[0],
            "enrichment_status": status,
            "enriched_at": now_iso(),
        }
    )


def test_refresh_missing_marks_not_found_games_pending(repo):
    _game(repo, "steam_1", ENRICHMENT_NOT_FOUND)
    _game(repo, "gog_2", ENRICHMENT_NOT_FOUND)
    _game(repo, "steam_3", ENRICHMENT_DONE)
    for client in _client(repo):
        response = client.post("/games/refresh-igdb")
        assert response.status_code == 200

    # The not_found games are flipped to pending so the enricher will pick them
    # up; the already-enriched game is left untouched.
    assert repo.get_game("steam_1")["enrichment_status"] == ENRICHMENT_PENDING
    assert repo.get_game("gog_2")["enrichment_status"] == ENRICHMENT_PENDING
    assert repo.get_game("steam_3")["enrichment_status"] == ENRICHMENT_DONE


def test_refresh_missing_queues_only_not_found_games(repo):
    _game(repo, "steam_1", ENRICHMENT_NOT_FOUND)
    _game(repo, "steam_3", ENRICHMENT_DONE)
    for client in _client(repo):
        assert client.post("/games/refresh-igdb").status_code == 200

    job = repo.get_active_job()
    assert job is not None
    assert job["release_keys"] == ["steam_1"]


def test_refresh_missing_with_nothing_to_do_creates_no_job(repo):
    _game(repo, "steam_3", ENRICHMENT_DONE)
    for client in _client(repo):
        assert client.post("/games/refresh-igdb").status_code == 200

    assert repo.get_active_job() is None
