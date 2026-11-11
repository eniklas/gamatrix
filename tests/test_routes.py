"""Tests for request-option parsing in the games routes.

Focus on how `web.parse_options` resolves boolean filter checkboxes. An unchecked
HTML checkbox submits no value, so the filter form sends a hidden
`filters_active` marker; when present, an absent flag means "off" rather than
falling back to the user's saved preference.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import types
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from gamatrix.app import app
from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.constants import PLATFORMS
from gamatrix.games import web


def _include_all() -> str:
    """Query fragment with every platform's Include box checked."""
    return "&".join(f"include={p}" for p in PLATFORMS)


def _opts(query_string: str, preferences: dict):
    request = types.SimpleNamespace(query_params=QueryParams(query_string))
    repo = types.SimpleNamespace(scan_users=lambda: [])
    return web.parse_options(request, {"preferences": preferences}, repo)


def test_unchecked_box_overrides_saved_on_preference():
    """Submitting the form with the box unchecked turns the option off even when
    the saved preference is on (the reported single-player bug)."""
    prefs = {"include_single_player": True, "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.include_single_player is False


def test_checked_box_turns_option_on():
    prefs = {"include_single_player": False, "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1&single_player=true", prefs)
    assert opts.include_single_player is True


def test_bare_page_load_uses_saved_preference():
    """A bare /games load (no filter form submitted) still honors saved prefs."""
    prefs = {"include_single_player": True, "selected_users": ["1"]}
    opts = _opts("user=1", prefs)
    assert opts.include_single_player is True


def test_marker_applies_to_all_boolean_flags():
    """All boolean filter checkboxes share the same resolution, so an absent one
    on a submitted form is off regardless of saved prefs."""
    prefs = {
        "include_single_player": True,
        "installed_only": True,
        "exclusive": True,
        "selected_users": ["1"],
    }
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.include_single_player is False
    assert opts.installed_only is False
    assert opts.exclusive is False


def test_all_included_clears_saved_exclude():
    """Submitting with every Include-platform box checked clears the saved
    exclude list instead of falling back to it."""
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts(f"user=1&filters_active=1&{_include_all()}", prefs)
    assert opts.exclude_platforms == []


def test_no_include_on_submit_excludes_all():
    """Submitting with every Include-platform box unchecked excludes them all
    (the inverse of the old all-unchecked-exclude case)."""
    prefs = {"exclude_platforms": [], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.exclude_platforms == list(PLATFORMS)


def test_partial_include_excludes_the_rest():
    """Only the checked platforms are kept; everything else is excluded, in the
    canonical PLATFORMS order."""
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1&include=steam&include=gog", prefs)
    assert opts.exclude_platforms == [p for p in PLATFORMS if p not in {"steam", "gog"}]


def test_bare_load_uses_saved_exclude_list():
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1", prefs)
    assert opts.exclude_platforms == ["gog", "epic"]


def test_deselecting_all_users_shows_none_on_submit():
    """Submitting with no user boxes checked yields an empty selection rather
    than falling back to the saved users (and without hitting the repo)."""
    prefs = {"selected_users": ["1", "2"], "exclude_platforms": []}
    opts = _opts("filters_active=1", prefs)
    assert opts.selected_user_ids == []


def test_bare_load_uses_saved_user_selection():
    prefs = {"selected_users": ["1", "2"], "exclude_platforms": []}
    opts = _opts("", prefs)
    assert opts.selected_user_ids == ["1", "2"]


def test_invalid_presentation_options_fall_back_to_safe_values():
    opts = _opts("view=unknown&dir=sideways", {})
    assert opts.view == "list"
    assert opts.direction == "asc"


def test_present_games_randomizes_only_in_web_layer():
    dataset = types.SimpleNamespace(
        items=[
            types.SimpleNamespace(to_dict=lambda: {"title": "A"}),
            types.SimpleNamespace(to_dict=lambda: {"title": "B"}),
        ]
    )
    opts = web.WebCompareOptions(randomize=True)
    games = web.present_games(dataset, opts)
    assert len(games) == 1
    assert games[0]["title"] in {"A", "B"}


def test_api_games_returns_headless_dataset(repo):
    repo.put_user({"email": "viewer@x.com", "username": "Viewer", "user_id": "99"})
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})
    repo.put_game(
        {
            "release_key": "steam_10",
            "title": "Coop Game",
            "slug": "coopgame",
            "igdb_key": "steam_10",
            "platform": "steam",
            "multiplayer": True,
            "max_players": 4,
            "rating": 90,
            "game_modes": [],
            "enrichment_status": "done",
        }
    )
    repo.replace_user_library(
        "1",
        [{"release_key": "steam_10", "platform": "steam", "installed": True}],
    )
    repo.replace_user_library(
        "2",
        [{"release_key": "steam_10", "platform": "steam", "installed": False}],
    )

    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: {
        "email": "viewer@x.com",
        "username": "Viewer",
    }
    try:
        client = TestClient(app)
        response = client.get("/api/games?user=1&user=2")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"]["scope"] == "shared"
    assert payload["total"] == 1
    assert payload["games"][0]["title"] == "Coop Game"


def _comment_route_game(repo) -> dict:
    user = {"email": "viewer@x.com", "username": "Viewer", "user_id": "99"}
    repo.put_user(user)
    repo.put_game(
        {
            "release_key": "steam_10",
            "title": "Coop Game",
            "slug": "coopgame",
            "igdb_key": "steam_10",
            "platform": "steam",
            "multiplayer": True,
            "max_players": 4,
        }
    )
    repo.put_metadata(
        {
            "slug": "coopgame",
            "max_players": 4,
            "comment": "Original",
            "comment_revision": 1,
        }
    )
    return user


def test_game_comment_can_be_edited_and_cleared(repo):
    user = _comment_route_game(repo)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        client = TestClient(app)
        edit = client.get("/games/steam_10/comment/edit")
        saved = client.post(
            "/games/steam_10/comment",
            data={"comment": "Use https://example.com/mod", "expected_revision": "1"},
        )
        cleared = client.post(
            "/games/steam_10/comment/clear",
            data={"expected_revision": "2"},
        )
    finally:
        app.dependency_overrides.clear()

    assert edit.status_code == 200
    assert "<textarea" in edit.text
    assert "Original" in edit.text
    assert saved.status_code == 200
    assert "commentChanged" in saved.headers["hx-trigger-after-swap"]
    assert 'href="https://example.com/mod"' in saved.text
    assert 'title="Viewer edited ' in saved.text
    assert cleared.status_code == 200
    assert "commentChanged" in cleared.headers["hx-trigger-after-swap"]
    assert 'aria-label="add/edit comment"' in cleared.text
    metadata = repo.get_metadata("coopgame")
    assert "comment" not in metadata
    assert metadata["max_players"] == 4
    assert metadata["comment_revision"] == 3


def test_game_comment_rejects_blank_stale_and_oversized_edits(repo):
    user = _comment_route_game(repo)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        client = TestClient(app)
        blank = client.post(
            "/games/steam_10/comment",
            data={"comment": "  ", "expected_revision": "1"},
        )
        oversized = client.post(
            "/games/steam_10/comment",
            data={"comment": "x" * 513, "expected_revision": "1"},
        )
        first = client.post(
            "/games/steam_10/comment",
            data={"comment": "First edit", "expected_revision": "1"},
        )
        stale = client.post(
            "/games/steam_10/comment",
            data={"comment": "Stale edit", "expected_revision": "1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert blank.status_code == 422
    assert "Comment cannot be empty" in blank.text
    # The stored comment still exists, so the Clear the error points to stays.
    assert "/comment/clear" in blank.text
    assert oversized.status_code == 422
    assert "512 characters or fewer" in oversized.text
    assert first.status_code == 200
    assert stale.status_code == 409
    assert "changed while you were editing" in stale.text
    assert repo.get_metadata("coopgame")["comment"] == "First edit"


@pytest.mark.parametrize(
    "submitted,expected,status_code",
    [
        ("a" * 510 + "\r\nb", "a" * 510 + "\nb", 200),
        ("a\rb", "a\nb", 200),
        ("a" * 511 + "\r\nb", None, 422),
        ("\U0001f600" * 257, None, 422),
        ("\U0001f600" * 256, "\U0001f600" * 256, 200),
    ],
    ids=["crlf-at-limit", "lone-cr", "crlf-over-limit", "emoji-over", "emoji-at"],
)
def test_comment_limit_matches_normalized_textarea(
    repo, monkeypatch, submitted, expected, status_code
):
    user = _comment_route_game(repo)
    monkeypatch.setitem(app.dependency_overrides, get_repo, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, current_user_api, lambda: user)
    response = TestClient(app).post(
        "/games/steam_10/comment",
        data={"comment": submitted, "expected_revision": "1"},
    )
    assert response.status_code == status_code
    assert repo.get_metadata("coopgame")["comment"] == (
        expected if expected is not None else "Original"
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_comment_author_never_exposes_email(repo, monkeypatch, legacy):
    user = _comment_route_game(repo)
    user.pop("username")
    repo.put_user(user)
    monkeypatch.setitem(app.dependency_overrides, get_repo, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, current_user_api, lambda: user)
    if legacy:
        repo.put_metadata(
            {
                "slug": "coopgame",
                "comment": "Legacy comment",
                "comment_updated_by": user["email"],
                "comment_updated_at": "2026-11-07T05:00:00+00:00",
            }
        )
        response = TestClient(app).get("/games/steam_10/comment")
    else:
        response = TestClient(app).post(
            "/games/steam_10/comment",
            data={"comment": "New comment", "expected_revision": "1"},
        )
    assert response.status_code == 200
    assert user["email"] not in response.text
    assert "anonymous coward" in response.text


def test_comment_api_keeps_editor_identity_private(repo, monkeypatch):
    user = _comment_route_game(repo)
    repo.replace_user_library("99", [{"release_key": "steam_10"}])
    repo.put_metadata(
        {
            "slug": "coopgame",
            "comment": "Comment",
            "comment_updated_by": user["email"],
            "comment_updated_at": "2026-11-07T05:00:00+00:00",
            "comment_revision": 1,
        }
    )
    monkeypatch.setitem(app.dependency_overrides, get_repo, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, current_user_api, lambda: user)
    response = TestClient(app).get("/api/games?user=99")
    game = response.json()["games"][0]
    assert game["comment"] == "Comment"
    assert "comment_updated_by" not in game
    assert "comment_updated_at" not in game
    assert "comment_revision" not in game


@pytest.mark.parametrize("clear", [False, True])
def test_comment_write_uses_authoritative_result_without_second_reads(
    repo, monkeypatch, clear
):
    user = _comment_route_game(repo)
    monkeypatch.setitem(app.dependency_overrides, get_repo, lambda: repo)
    monkeypatch.setitem(app.dependency_overrides, current_user_api, lambda: user)
    with (
        patch.object(repo, "get_game", wraps=repo.get_game) as game_read,
        patch.object(repo, "get_metadata", wraps=repo.get_metadata) as metadata_read,
    ):
        response = TestClient(app).post(
            "/games/steam_10/comment" + ("/clear" if clear else ""),
            data={"comment": "Authoritative", "expected_revision": "1"},
        )
    assert response.status_code == 200
    assert game_read.call_count == 1
    assert metadata_read.call_count == 1
    if not clear:
        assert "Authoritative" in response.text


def test_authenticated_ux_routes_remain_auth_gated(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        client = TestClient(app)
        for path in (
            "/games",
            "/preferences",
            "/upload",
            "/auth/passkeys",
            "/auth/tokens",
        ):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == "/auth/login"

        for path in (
            "/games/table",
            "/api/games",
            "/api/jobs/not-found",
            "/games/steam_10/comment",
            "/games/steam_10/comment/edit",
        ):
            assert client.get(path).status_code == 401

        for path in (
            "/games/refresh-igdb",
            "/games/refresh-igdb-all",
            "/games/steam_10/comment",
            "/games/steam_10/comment/clear",
            "/library/platforms/steam/remove",
        ):
            assert client.post(path).status_code == 401
    finally:
        app.dependency_overrides.clear()


def _upload_page_user(repo) -> dict:
    """A user with a two-platform library, one platform kept from an older DB."""
    repo.put_user(
        {
            "email": "viewer@x.com",
            "user_id": "1",
            "username": "Viewer",
            "db_updated_at": "2026-01-02T00:00:00+00:00",
        }
    )
    repo.replace_user_library(
        "1",
        [
            {
                "release_key": "steam_10",
                "platform": "steam",
                "installed": True,
                "db_updated_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "release_key": "epic_9",
                "platform": "epic",
                "installed": False,
                "db_updated_at": "2025-06-01T00:00:00+00:00",
            },
        ],
    )
    return repo.get_user("viewer@x.com")


def test_upload_page_lists_platforms_and_flags_kept_ones(repo):
    user = _upload_page_user(repo)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user] = lambda: user
    try:
        response = TestClient(app).get("/upload")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "steam" in response.text and "epic" in response.text
    # Only the platform missing from the latest upload is called out as kept.
    assert "not in your latest upload" in response.text


def test_remove_library_platform_drops_only_that_platform(repo):
    user = _upload_page_user(repo)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        response = TestClient(app).post("/library/platforms/epic/remove")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Removed 1 epic title" in response.text
    assert {row["release_key"] for row in repo.get_user_library("1")} == {"steam_10"}


def test_lambda_responses_are_gzipped_for_clients_that_accept_it(repo):
    """Large pages must go out compressed: Lambda rejects responses over 6 MB,
    and the all-games grid for a big group exceeds that as plain HTML."""
    from gamatrix.lambda_handler import handler

    user = _upload_page_user(repo)

    def event(accept_encoding: str | None) -> dict:
        headers = {"host": "testserver"}
        if accept_encoding:
            headers["accept-encoding"] = accept_encoding
        return {
            "version": "2.0",
            "routeKey": "$default",
            "rawPath": "/upload",
            "rawQueryString": "",
            "headers": headers,
            "requestContext": {
                "http": {
                    "method": "GET",
                    "path": "/upload",
                    "protocol": "HTTP/1.1",
                    "sourceIp": "203.0.113.1",
                    "userAgent": "pytest",
                },
                "domainName": "testserver",
                "stage": "$default",
            },
            "isBase64Encoded": False,
        }

    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user] = lambda: user
    # Mangum runs on the thread's current loop; async tests may have closed it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        compressed = handler(event("gzip, deflate, br"), types.SimpleNamespace())
        plain = handler(event(None), types.SimpleNamespace())
    finally:
        app.dependency_overrides.clear()
        asyncio.set_event_loop(None)
        loop.close()

    assert compressed["statusCode"] == 200
    assert compressed["headers"]["content-encoding"] == "gzip"
    assert compressed["isBase64Encoded"] is True
    html = gzip.decompress(base64.b64decode(compressed["body"])).decode()
    assert html == plain["body"]
    assert "not in your latest upload" in html
    assert plain["isBase64Encoded"] is False
    assert "content-encoding" not in plain["headers"]
