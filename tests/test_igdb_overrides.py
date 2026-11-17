"""Manual IGDB slug overrides.

IGDB's slugs aren't derivable from titles, so an admin can pin the right one on
the title's metadata_overrides row. These tests cover the client honouring it
(with no fallback), the enricher reading it fresh, the repository keeping it
through a metadata wipe, and the /admin/igdb page.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import gamatrix.igdb.enricher as enricher
from gamatrix.admin import normalize_igdb_slug
from gamatrix.app import app
from gamatrix.auth.dependencies import (
    current_user,
    current_user_api,
    get_repo,
)
from gamatrix.constants import (
    ENRICHMENT_DONE,
    ENRICHMENT_NOT_FOUND,
    ENRICHMENT_PENDING,
    IGDB_API_CALL_DELAY,
    JOB_COMPLETED,
    JOB_FAILED,
)
from gamatrix.igdb.client import GameMetadata, IGDBClient
from gamatrix.igdb.enricher import run_job
from gamatrix.jobs import create_enrichment_job

ADMIN = {"email": "admin@example.com", "user_id": "1", "is_admin": True}
MEMBER = {"email": "member@example.com", "user_id": "2", "is_admin": False}


# ----------------------------------------------------------------------
# IGDB client
# ----------------------------------------------------------------------
class _FakeClient(IGDBClient):
    def __init__(self, responses):
        self._responses = responses
        self.access_token = "fake"
        self.calls = []

    async def _query(self, endpoint, body):  # type: ignore[override]
        self.calls.append((endpoint, body))
        return self._responses.get(endpoint, [])


async def test_manual_slug_is_the_only_lookup():
    client = _FakeClient({"external_games": [{"game": 42}], "games": [{"id": 7}]})
    assert await client.resolve_igdb_id("steam_1", "Alpha", "alpha--1") == 7
    assert client.calls == [("games", 'fields id; where slug = "alpha--1";')]


async def test_manual_slug_miss_does_not_fall_back():
    # The uid lookup would have matched, but a manual slug is authoritative:
    # a wrong one must surface as not-found, not be hidden by auto-matching.
    client = _FakeClient({"external_games": [{"game": 42}], "games": []})
    meta = await client.fetch_metadata("steam_1", "Alpha", "no-such-game")
    assert meta.found is False
    assert [endpoint for endpoint, _ in client.calls] == ["games"]


@pytest.mark.parametrize("bad", ['alpha" | id > 0', "alpha;", "Alpha", "-alpha"])
async def test_malformed_manual_slug_never_reaches_igdb(bad):
    # A slug edited straight into DynamoDB skips the admin page's check; the
    # client must not interpolate it into a query, and (being authoritative)
    # must not fall back either: it's simply not found.
    client = _FakeClient({"external_games": [{"game": 42}], "games": [{"id": 7}]})
    assert await client.resolve_igdb_id("steam_1", "Alpha", bad) == 0
    assert client.calls == []


# ----------------------------------------------------------------------
# Enricher
# ----------------------------------------------------------------------
class _RecordingIGDBClient:
    calls: list[tuple[str, str, str | None]] = []

    def __init__(self, client_id, client_secret, call_delay=IGDB_API_CALL_DELAY):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_metadata(self, igdb_key, title, igdb_slug=None):
        type(self).calls.append((igdb_key, title, igdb_slug))
        return GameMetadata(
            igdb_id=7, name="Alpha Real", slug="alpha--1", max_players=4, found=True
        )


class _NullQueue:
    def enqueue(self, job_id, chunk_index):
        pass


def _game(repo, rk, slug, status=ENRICHMENT_PENDING, **extra):
    repo.put_game(
        {
            "release_key": rk,
            "title": extra.pop("title", slug.replace("-", " ").title()),
            "slug": slug,
            "igdb_key": rk,
            "platform": rk.split("_")[0],
            "enrichment_status": status,
            **extra,
        }
    )


async def test_enricher_passes_fresh_manual_slug(repo, monkeypatch):
    _RecordingIGDBClient.calls = []
    monkeypatch.setattr(enricher, "IGDBClient", _RecordingIGDBClient)
    monkeypatch.setattr(
        enricher, "resolve_igdb_credentials", lambda settings: ("id", "secret")
    )
    _game(repo, "steam_1", "alpha")
    # Prime the cached read model, then set the slug behind its back (as
    # another process would); the enricher must still see it.
    assert repo.get_all_metadata() == {}
    repo._table(repo.settings.metadata_table).put_item(
        Item={"slug": "alpha", "igdb_slug": "alpha--1"}
    )
    job_id = create_enrichment_job(repo, _NullQueue(), ["steam_1"])

    await run_job(job_id, repo)

    assert _RecordingIGDBClient.calls == [("steam_1", "Alpha", "alpha--1")]
    game = repo.get_game("steam_1")
    assert game["enrichment_status"] == ENRICHMENT_DONE
    assert game["igdb_name"] == "Alpha Real"
    assert game["igdb_matched_slug"] == "alpha--1"
    assert game["max_players"] == 4


async def test_enricher_applies_slug_from_any_title_in_igdb_key_group(
    repo, monkeypatch
):
    # Galaxy links these as one game (shared igdb_key) under different titles;
    # the override sits on the second title, not the first copy's.
    _RecordingIGDBClient.calls = []
    monkeypatch.setattr(enricher, "IGDBClient", _RecordingIGDBClient)
    monkeypatch.setattr(
        enricher, "resolve_igdb_credentials", lambda settings: ("id", "secret")
    )
    _game(repo, "gog_1", "alpha", igdb_key="gog_1")
    _game(repo, "epic_1", "alpha-goty", igdb_key="gog_1", title="Alpha GOTY")
    repo.set_metadata_igdb_slug("alpha-goty", "alpha--1")
    job_id = create_enrichment_job(repo, _NullQueue(), ["gog_1", "epic_1"])

    await run_job(job_id, repo)

    assert [c[2] for c in _RecordingIGDBClient.calls] == ["alpha--1"]


# ----------------------------------------------------------------------
# Repository
# ----------------------------------------------------------------------
def test_set_igdb_slug_leaves_comment_alone(repo):
    repo.update_metadata_comment("alpha", "great co-op", "admin", 0)
    repo.set_metadata_igdb_slug("alpha", "alpha--1")
    row = repo.get_metadata("alpha")
    assert row["igdb_slug"] == "alpha--1"
    assert row["comment"] == "great co-op"
    assert row["comment_revision"] == 1

    repo.set_metadata_igdb_slug("alpha", None)
    row = repo.get_metadata("alpha")
    assert "igdb_slug" not in row
    assert row["comment"] == "great co-op"


def test_clearing_a_slug_without_a_row_creates_nothing(repo):
    repo.set_metadata_igdb_slug("ghost", None)
    assert repo.get_metadata("ghost") is None


def test_metadata_wipe_keeps_igdb_slugs(repo):
    repo.import_metadata({"slug": "alpha", "max_players": 4})
    repo.set_metadata_igdb_slug("alpha", "alpha--1")
    repo.import_metadata({"slug": "beta", "max_players": 2})

    repo.reset_imported_metadata()

    assert repo.get_metadata("beta") is None
    row = repo.get_metadata("alpha")
    assert row["igdb_slug"] == "alpha--1"
    assert "max_players" not in row


# ----------------------------------------------------------------------
# Slug input
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("alpha--1", "alpha--1"),
        ("  Hollow-Knight ", "hollow-knight"),
        ("https://www.igdb.com/games/hollow-knight", "hollow-knight"),
        ("https://www.igdb.com/games/hollow-knight/", "hollow-knight"),
        ("igdb.com/games/hollow-knight?tab=info", "hollow-knight"),
        ("https://example.com/games/hollow-knight", None),
        ("https://www.igdb.com/companies/team-cherry", None),
        ('foo"; fields *;', None),
        ("", None),
        ("-", None),
    ],
)
def test_normalize_igdb_slug(raw, expected):
    assert normalize_igdb_slug(raw) == expected


# ----------------------------------------------------------------------
# Admin page
# ----------------------------------------------------------------------
@pytest.fixture
def client_as(repo):
    def make(user):
        app.dependency_overrides[get_repo] = lambda: repo
        if user is not None:
            app.dependency_overrides[current_user] = lambda: user
            app.dependency_overrides[current_user_api] = lambda: user
        return TestClient(app, follow_redirects=False)

    yield make
    app.dependency_overrides.clear()


def test_page_redirects_anonymous_to_login(client_as):
    response = client_as(None).get("/admin/igdb")
    assert response.status_code == 302
    assert response.headers["location"] == "/auth/login"


def test_page_and_actions_refuse_non_admins(client_as):
    client = client_as(MEMBER)
    assert client.get("/admin/igdb").status_code == 403
    assert client.get("/admin/igdb/list").status_code == 403
    response = client.post("/admin/igdb/alpha", data={"igdb_slug": "alpha--1"})
    assert response.status_code == 403


def test_page_lists_misses_and_overrides(repo, client_as):
    _game(repo, "steam_1", "alpha", ENRICHMENT_NOT_FOUND)
    _game(repo, "steam_2", "beta", ENRICHMENT_DONE)
    _game(repo, "steam_3", "gamma", ENRICHMENT_DONE)
    repo.set_metadata_igdb_slug("gamma", "gamma-2020")

    html = client_as(ADMIN).get("/admin/igdb").text

    assert "Alpha" in html
    assert "Beta" not in html
    assert "Gamma" in html and 'value="gamma-2020"' in html


def test_search_covers_matched_games(repo, client_as):
    _game(
        repo,
        "steam_2",
        "beta",
        ENRICHMENT_DONE,
        igdb_id=9,
        igdb_name="Beta Wrong",
        igdb_matched_slug="beta-wrong",
    )
    _game(repo, "steam_3", "gamma", ENRICHMENT_DONE)

    html = client_as(ADMIN).get("/admin/igdb/list", params={"q": "BET"}).text

    assert "https://www.igdb.com/games/beta-wrong" in html
    assert "Gamma" not in html


def test_save_stores_slug_and_reenriches_every_copy(repo, client_as):
    _game(repo, "steam_1", "alpha", ENRICHMENT_NOT_FOUND)
    _game(repo, "gog_1", "alpha", ENRICHMENT_NOT_FOUND)
    _game(repo, "steam_2", "beta", ENRICHMENT_NOT_FOUND)

    response = client_as(ADMIN).post(
        "/admin/igdb/alpha",
        data={"igdb_slug": "https://www.igdb.com/games/alpha--1", "q": ""},
    )

    assert response.status_code == 200
    assert "job-progress" in response.text
    assert repo.get_metadata("alpha")["igdb_slug"] == "alpha--1"
    assert repo.get_game("steam_1")["enrichment_status"] == ENRICHMENT_PENDING
    assert repo.get_game("gog_1")["enrichment_status"] == ENRICHMENT_PENDING
    assert repo.get_game("steam_2")["enrichment_status"] == ENRICHMENT_NOT_FOUND
    job = repo.get_active_job()
    assert sorted(job["release_keys"]) == ["gog_1", "steam_1"]


def test_save_reenriches_linked_copies_under_other_titles(repo, client_as):
    _game(repo, "gog_1", "alpha", ENRICHMENT_NOT_FOUND, igdb_key="gog_1")
    _game(
        repo,
        "epic_1",
        "alpha-goty",
        ENRICHMENT_NOT_FOUND,
        igdb_key="gog_1",
        title="Alpha GOTY",
    )
    _game(repo, "steam_2", "beta", ENRICHMENT_NOT_FOUND)

    client_as(ADMIN).post("/admin/igdb/alpha", data={"igdb_slug": "alpha--1"})

    assert sorted(repo.get_active_job()["release_keys"]) == ["epic_1", "gog_1"]
    assert repo.get_game("epic_1")["enrichment_status"] == ENRICHMENT_PENDING


def test_search_reuses_the_cached_read_model(repo, client_as, monkeypatch):
    _game(repo, "steam_1", "alpha", ENRICHMENT_NOT_FOUND)
    client = client_as(ADMIN)
    client.get("/admin/igdb/list", params={"q": "a"})
    scans = []
    real_scan = repo._scan
    monkeypatch.setattr(
        repo, "_scan", lambda table: scans.append(table) or real_scan(table)
    )

    for q in ("al", "alp", "alph"):
        assert "Alpha" in client.get("/admin/igdb/list", params={"q": q}).text

    assert scans == []


def test_fresh_list_sees_writes_from_another_process(repo, client_as):
    _game(repo, "steam_1", "alpha", ENRICHMENT_NOT_FOUND)
    client = client_as(ADMIN)
    assert "Alpha" in client.get("/admin/igdb/list").text
    # The enricher (another process) writes the match behind the cache's back.
    repo._table(repo.settings.games_table).put_item(
        Item={
            "release_key": "steam_1",
            "slug": "alpha",
            "title": "Alpha",
            "igdb_key": "steam_1",
            "enrichment_status": ENRICHMENT_DONE,
        }
    )

    assert "Alpha" in client.get("/admin/igdb/list").text  # cached
    assert "Alpha" not in client.get("/admin/igdb/list?fresh=1").text


def test_save_rejects_junk(repo, client_as):
    _game(repo, "steam_1", "alpha", ENRICHMENT_NOT_FOUND)

    response = client_as(ADMIN).post(
        "/admin/igdb/alpha", data={"igdb_slug": "https://example.com/x"}
    )

    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert repo.get_metadata("alpha") is None
    assert repo.get_active_job() is None


def test_clear_removes_slug_and_reenriches(repo, client_as):
    _game(repo, "steam_1", "alpha", ENRICHMENT_DONE)
    repo.update_metadata_comment("alpha", "keep me", "admin", 0)
    repo.set_metadata_igdb_slug("alpha", "alpha--1")

    response = client_as(ADMIN).post("/admin/igdb/alpha/clear", data={"q": ""})

    assert response.status_code == 200
    row = repo.get_metadata("alpha")
    assert "igdb_slug" not in row
    assert row["comment"] == "keep me"
    assert repo.get_game("steam_1")["enrichment_status"] == ENRICHMENT_PENDING
    assert repo.get_active_job()["release_keys"] == ["steam_1"]


def test_job_poll_reloads_list_when_done(repo, client_as):
    job_id = create_enrichment_job(repo, _NullQueue(), ["steam_1"])
    repo.update_job(job_id, {"status": JOB_COMPLETED})

    html = client_as(ADMIN).get(f"/admin/igdb/jobs/{job_id}").text

    assert 'hx-get="/admin/igdb/list?fresh=1"' in html
    assert 'hx-trigger="load"' in html
    assert 'role="alert"' not in html


def test_job_poll_reports_failure_instead_of_reloading(repo, client_as):
    job_id = create_enrichment_job(repo, _NullQueue(), ["steam_1"])
    repo.update_job(job_id, {"status": JOB_FAILED})

    html = client_as(ADMIN).get(f"/admin/igdb/jobs/{job_id}").text

    assert 'role="alert"' in html
    assert 'hx-trigger="load"' not in html
    assert "every 3s" not in html


def test_games_page_links_admins_to_fixer(repo, client_as):
    assert "/admin/igdb" in client_as(ADMIN).get("/games").text
    assert "/admin/igdb" not in client_as(MEMBER).get("/games").text
