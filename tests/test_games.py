"""Tests for the games comparison service."""

from __future__ import annotations

import pytest

from gamatrix.games.service import CompareOptions, compare
from gamatrix.helpers import now_iso


@pytest.fixture
def populated(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})

    def game(rk, title, slug, mp, maxp, rating=0):
        repo.put_game(
            {
                "release_key": rk,
                "title": title,
                "slug": slug,
                "igdb_key": rk,
                "platform": rk.split("_")[0],
                "multiplayer": mp,
                "max_players": maxp,
                "rating": rating,
                "game_modes": [],
                "enrichment_status": "done",
                "enriched_at": now_iso(),
            }
        )

    game("steam_10", "Coop Game", "coopgame", True, 4, rating=90)
    game("steam_11", "Solo Game", "sologame", False, 1)
    game("gog_12", "Shared MP", "sharedmp", True, 8, rating=75)

    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_10", "platform": "steam", "installed": True},
            {"release_key": "steam_11", "platform": "steam", "installed": False},
            {"release_key": "gog_12", "platform": "gog", "installed": False},
        ],
    )
    repo.replace_user_library(
        "2",
        [
            {"release_key": "steam_10", "platform": "steam", "installed": True},
            {"release_key": "gog_12", "platform": "gog", "installed": True},
        ],
    )
    return repo


def test_common_multiplayer_games(populated):
    result = compare(populated, CompareOptions(selected_user_ids=["1", "2"]))
    titles = {g["title"] for g in result.games}
    assert titles == {"Coop Game", "Shared MP"}


def test_include_single_player_single_user(populated):
    result = compare(
        populated,
        CompareOptions(selected_user_ids=["1"], include_single_player=True),
    )
    titles = {g["title"] for g in result.games}
    assert titles == {"Coop Game", "Solo Game", "Shared MP"}


def test_installed_only(populated):
    result = compare(
        populated,
        CompareOptions(selected_user_ids=["1"], installed_only=True),
    )
    # Only steam_10 is installed by user 1 (and it's multiplayer).
    titles = {g["title"] for g in result.games}
    assert titles == {"Coop Game"}


def test_exclude_platform(populated):
    result = compare(
        populated,
        CompareOptions(selected_user_ids=["1", "2"], exclude_platforms=["gog"]),
    )
    titles = {g["title"] for g in result.games}
    assert titles == {"Coop Game"}


def test_sort_by_rating_desc(populated):
    result = compare(
        populated,
        CompareOptions(selected_user_ids=["1", "2"], sort="rating", direction="desc"),
    )
    ratings = [g["rating"] for g in result.games]
    assert ratings == sorted(ratings, reverse=True)


def test_metadata_override_applied(populated):
    populated.put_metadata(
        {"slug": "coopgame", "max_players": 2, "comment": "Use Hamachi"}
    )
    result = compare(populated, CompareOptions(selected_user_ids=["1", "2"]))
    coop = next(g for g in result.games if g["slug"] == "coopgame")
    assert coop["max_players"] == 2
    assert coop["comment"] == "Use Hamachi"


def test_merge_cross_platform_duplicates(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    for rk, platform in [("steam_30", "steam"), ("gog_31", "gog")]:
        repo.put_game(
            {
                "release_key": rk,
                "title": "Dup Game",
                "slug": "dupgame",
                "igdb_key": rk,
                "platform": platform,
                "multiplayer": True,
                "max_players": 4,
                "rating": 0,
                "game_modes": [],
                "enrichment_status": "done",
            }
        )
    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_30", "platform": "steam", "installed": False},
            {"release_key": "gog_31", "platform": "gog", "installed": False},
        ],
    )
    result = compare(repo, CompareOptions(selected_user_ids=["1"]))
    assert len(result.games) == 1
    assert sorted(result.games[0]["platforms"]) == ["gog", "steam"]
