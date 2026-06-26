"""Tests for request-option parsing in the games routes.

Focus on how `web.parse_options` resolves boolean filter checkboxes. An unchecked
HTML checkbox submits no value, so the filter form sends a hidden
`filters_active` marker; when present, an absent flag means "off" rather than
falling back to the user's saved preference.
"""

from __future__ import annotations

import types

from fastapi.testclient import TestClient
from starlette.datastructures import QueryParams

from gamatrix.app import app
from gamatrix.auth.dependencies import current_user_api, get_repo
from gamatrix.games import web


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


def test_emptied_exclude_list_clears_saved_pref():
    """Submitting with every platform-exclude box unchecked clears the saved
    exclude list instead of falling back to it."""
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.exclude_platforms == []


def test_partial_exclude_list_wins_over_saved_pref():
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1&exclude=gog", prefs)
    assert opts.exclude_platforms == ["gog"]


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

        for path in ("/games/table", "/api/games", "/api/jobs/not-found"):
            assert client.get(path).status_code == 401

        for path in ("/games/refresh-igdb", "/games/refresh-igdb-all"):
            assert client.post(path).status_code == 401
    finally:
        app.dependency_overrides.clear()
